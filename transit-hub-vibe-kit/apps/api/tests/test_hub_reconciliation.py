import json
from uuid import UUID, uuid4

import pytest

from app.domain.enums import (
    CoordinateSystem,
    HubReconciliationReasonCode,
    HubReconciliationStatus,
    HubType,
)
from app.domain.models import Coordinate, HubCandidate, ProviderReference
from app.domain.normalization import normalize_hub_name
from app.domain.reconciliation import CanonicalHubRecord, ProviderHubCandidate
from app.providers.rail_gtfs.schemas import GTFSStop
from app.providers.rail_gtfs.station_mapper import CanonicalHubIndex
from app.services.hub_reconciliation import HubReconciliationIndex, HubReconciliationService


def _record(
    name: str,
    city: str,
    hub_type: HubType,
    *,
    city_id: UUID | None = None,
    aliases: tuple[str, ...] = (),
    hub_id: UUID | None = None,
) -> CanonicalHubRecord:
    return CanonicalHubRecord(
        hub_id=hub_id or uuid4(),
        city_id=city_id or uuid4(),
        city_name=city,
        canonical_name=name,
        normalized_name=normalize_hub_name(name, hub_type),
        aliases=aliases,
        hub_type=hub_type,
    )


def _candidate(
    name: str,
    hub_type: HubType,
    *,
    city: str | None = None,
    provider: str = "TEST_PROVIDER",
    provider_id: str | None = None,
    aliases: tuple[str, ...] = (),
) -> ProviderHubCandidate:
    return ProviderHubCandidate(
        provider=provider,
        provider_hub_id=provider_id or f"provider:{name}",
        provider_object_type="HUB",
        hub_type=hub_type,
        name=name,
        city_name_zh=city,
        aliases=aliases,
    )


def _index(
    records: list[CanonicalHubRecord],
    refs: dict[tuple[str, str], set[UUID]] | None = None,
) -> HubReconciliationIndex:
    return HubReconciliationIndex(records, refs or {})


def test_provider_reference_is_stronger_than_name_matching() -> None:
    chengdu = _record("成都东站", "成都", HubType.RAILWAY)
    other = _record("同名站", "乐山", HubType.RAILWAY)
    index = _index(
        [chengdu, other],
        {("TEST_PROVIDER", "station-1"): {chengdu.hub_id}},
    )

    result = index.reconcile(_candidate("完全不同的名称", HubType.RAILWAY, provider_id="station-1"))

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.canonical_hub_id == chengdu.hub_id
    assert result.reason_code == HubReconciliationReasonCode.PROVIDER_REF
    assert result.match_strategy == "provider_ref"


def test_provider_and_object_id_are_scoped_together() -> None:
    record = _record("成都东站", "成都", HubType.RAILWAY)
    index = _index([record], {("OTHER_PROVIDER", "station-1"): {record.hub_id}})

    result = index.reconcile(_candidate("未知枢纽", HubType.RAILWAY, provider_id="station-1"))

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.NO_MATCH


def test_conflicting_provider_reference_is_ambiguous() -> None:
    first = _record("成都东站", "成都", HubType.RAILWAY)
    second = _record("成都南站", "成都", HubType.RAILWAY)
    index = _index(
        [first, second],
        {("TEST_PROVIDER", "station-1"): {first.hub_id, second.hub_id}},
    )

    result = index.reconcile(_candidate("任何名称", HubType.RAILWAY, provider_id="station-1"))

    assert result.status == HubReconciliationStatus.AMBIGUOUS
    assert result.reason_code == HubReconciliationReasonCode.PROVIDER_REF_CONFLICT
    assert set(result.candidate_hub_ids) == {first.hub_id, second.hub_id}
    assert result.candidate_names == ("成都东站", "成都南站")


def test_provider_reference_type_conflict_is_not_retargeted_by_name() -> None:
    airport = _record("成都天府国际机场", "成都", HubType.AIRPORT)
    index = _index(
        [airport],
        {("TEST_PROVIDER", "object-1"): {airport.hub_id}},
    )

    result = index.reconcile(
        _candidate("成都天府国际机场", HubType.RAILWAY, provider_id="object-1")
    )

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.PROVIDER_REF_TYPE_CONFLICT


def test_exact_alias_and_suffix_normalization_are_deterministic() -> None:
    station = _record("南京南站", "南京", HubType.RAILWAY, aliases=("南京南",))
    index = _index([station])

    exact = index.reconcile(_candidate("南京南", HubType.RAILWAY))
    suffix = index.reconcile(_candidate("南京南站", HubType.RAILWAY))

    assert exact.status == HubReconciliationStatus.MATCHED
    assert exact.match_strategy == "exact_alias"
    assert suffix.status == HubReconciliationStatus.MATCHED
    assert suffix.match_strategy == "normalized_canonical_name"


def test_conflicting_name_strategies_remain_ambiguous() -> None:
    canonical = _record("成都东站", "成都", HubType.RAILWAY)
    alias_target = _record("成都南站", "成都", HubType.RAILWAY, aliases=("成都东",))
    index = _index([canonical, alias_target])

    result = index.reconcile(_candidate("成都东站", HubType.RAILWAY, aliases=("成都东",)))

    assert result.status == HubReconciliationStatus.AMBIGUOUS
    assert result.reason_code == HubReconciliationReasonCode.STRATEGY_CONFLICT
    assert result.match_strategy == "conflicting_name_strategies"
    assert set(result.candidate_hub_ids) == {canonical.hub_id, alias_target.hub_id}


def test_conflicting_city_name_strategy_does_not_silently_match() -> None:
    chengdu = _record("成都东站", "成都", HubType.RAILWAY)
    leshan = _record("旧称车站", "乐山", HubType.RAILWAY, aliases=("成都东",))
    index = _index([chengdu, leshan])

    result = index.reconcile(_candidate("成都东站", HubType.RAILWAY, city="成都"))

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.CITY_CONFLICT
    assert result.canonical_hub_id is None


def test_conflicting_city_id_and_name_do_not_silently_match() -> None:
    chengdu_city = uuid4()
    leshan_city = uuid4()
    station = _record("成都东站", "成都", HubType.RAILWAY, city_id=chengdu_city)
    index = _index([station])
    candidate = _candidate("成都东站", HubType.RAILWAY, city="乐山")
    candidate = candidate.model_copy(update={"city_id": leshan_city})

    result = index.reconcile(candidate)

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.CITY_CONFLICT


def test_unicode_nfkc_is_shared_by_generic_hub_normalization() -> None:
    station = _record("北京站", "北京", HubType.RAILWAY)
    index = _index([station])

    result = index.reconcile(_candidate("北京　站", HubType.RAILWAY, city="北京"))

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.canonical_hub_id == station.hub_id


def test_airport_suffixes_use_the_airport_policy() -> None:
    airport = _record(
        "成都天府国际机场",
        "成都",
        HubType.AIRPORT,
        aliases=("天府机场",),
    )
    index = _index([airport])

    result = index.reconcile(_candidate("成都天府机场", HubType.AIRPORT, city="成都市"))

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.canonical_hub_id == airport.hub_id


def test_hub_type_conflict_never_matches_by_name() -> None:
    railway = _record("成都东站", "成都", HubType.RAILWAY, aliases=("成都东",))
    index = _index([railway])

    result = index.reconcile(_candidate("成都东站", HubType.AIRPORT, city="成都"))

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.TYPE_CONFLICT
    assert result.canonical_hub_id is None


def test_city_context_disambiguates_same_normalized_name() -> None:
    nanjing = _record("南京南站", "南京", HubType.RAILWAY)
    another = _record("南京南站", "另一市", HubType.RAILWAY)
    index = _index([another, nanjing])

    result = index.reconcile(_candidate("南京南", HubType.RAILWAY, city="南京市"))

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.canonical_hub_id == nanjing.hub_id


def test_absent_city_keeps_equal_matches_ambiguous() -> None:
    first = _record("同名站", "成都", HubType.RAILWAY)
    second = _record("同名站", "乐山", HubType.RAILWAY)
    index = _index([first, second])

    result = index.reconcile(_candidate("同名站", HubType.RAILWAY))

    assert result.status == HubReconciliationStatus.AMBIGUOUS
    assert result.reason_code == HubReconciliationReasonCode.NORMALIZED_CANONICAL_NAME_AMBIGUOUS
    assert result.candidate_names == ("同名站", "同名站")


def test_contradictory_city_is_unresolved() -> None:
    chengdu = _record("成都东站", "成都", HubType.RAILWAY)
    index = _index([chengdu])

    result = index.reconcile(_candidate("成都东", HubType.RAILWAY, city="乐山"))

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.CITY_CONFLICT


def test_unknown_candidate_does_not_create_a_canonical_hub() -> None:
    index = _index([_record("成都东站", "成都", HubType.RAILWAY)])

    result = index.reconcile(_candidate("不存在站", HubType.RAILWAY, city="成都"))

    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.NO_MATCH
    assert result.candidate_hub_ids == ()


def test_wrong_provider_reference_does_not_override_a_safe_name_match() -> None:
    station = _record("成都东站", "成都", HubType.RAILWAY, aliases=("成都东",))
    index = _index([station], {("OTHER_PROVIDER", "same-id"): {station.hub_id}})

    result = index.reconcile(
        _candidate("成都东", HubType.RAILWAY, provider_id="same-id", city="成都")
    )

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.match_strategy == "exact_alias"


def test_amap_normalized_candidate_crosses_the_same_generic_boundary() -> None:
    canonical = _record("成都天府国际机场", "成都", HubType.AIRPORT, aliases=("天府机场",))
    index = _index([canonical])
    provider_candidate = HubCandidate(
        canonical_name_zh="成都天府机场",
        city_name_zh="成都市",
        hub_type=HubType.AIRPORT,
        coordinate=Coordinate(
            longitude=104.4,
            latitude=30.3,
            coordinate_system=CoordinateSystem.GCJ02,
        ),
        source="amap:poi",
        provider_reference=ProviderReference(
            provider="AMAP",
            provider_object_type="POI",
            provider_id="B000A",
        ),
    )

    result = index.reconcile(ProviderHubCandidate.from_hub_candidate(provider_candidate))

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.provider == "AMAP"
    assert result.canonical_hub_id == canonical.hub_id


def test_station_mapper_compatibility_preserves_legacy_report_shape() -> None:
    station = _record("南京南站", "南京", HubType.RAILWAY, aliases=("南京南",))
    index = CanonicalHubIndex([station], {})

    match = index.reconcile(GTFSStop("stop-1", "南京南"))

    assert match.status == "matched"
    assert match.hub_id == station.hub_id
    assert match.reason == "exact_alias"


def test_reconciliation_events_are_structured_and_safe(caplog: pytest.LogCaptureFixture) -> None:
    index = _index([_record("成都东站", "成都", HubType.RAILWAY)])

    index.reconcile(_candidate("未知站", HubType.RAILWAY, provider_id="secret-id"))

    events = [
        json.loads(record.message) for record in caplog.records if record.message.startswith("{")
    ]
    event = next(item for item in events if item["event"] == "hub.reconciliation.unresolved")
    assert event["status"] == "UNRESOLVED"
    assert event["reason_code"] == "NO_MATCH"
    assert event["duration_ms"] >= 0
    assert "secret-id" not in json.dumps(event)


def test_service_wrapper_accepts_normalized_provider_candidates() -> None:
    airport = _record("成都天府国际机场", "成都", HubType.AIRPORT)
    service = HubReconciliationService(_index([airport]))

    result = service.reconcile(_candidate("成都天府国际机场", HubType.AIRPORT, city="成都"))

    assert result.status == HubReconciliationStatus.MATCHED
    assert result.rules_version == "hub-reconciliation-v1"


def test_registry_insertion_order_does_not_change_ambiguous_diagnostics() -> None:
    first = _record("同名站", "成都", HubType.RAILWAY)
    second = _record("同名站", "乐山", HubType.RAILWAY)
    candidate = _candidate("同名站", HubType.RAILWAY)

    left = _index([first, second]).reconcile(candidate)
    right = _index([second, first]).reconcile(candidate)

    assert left.status == right.status == HubReconciliationStatus.AMBIGUOUS
    assert left.candidate_hub_ids == right.candidate_hub_ids
    assert left.candidate_names == right.candidate_names
