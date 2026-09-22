from uuid import uuid4

import pytest
from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError

from app.core.errors import AppError
from app.db.models import (
    City,
    Hub,
    HubProviderRef,
    HubReconciliationOverride,
    HubReconciliationOverrideEvent,
)
from app.domain.enums import HubReconciliationReasonCode, HubReconciliationStatus, HubType
from app.domain.models import Coordinate, HubCandidate, ProviderReference
from app.domain.reconciliation import (
    CanonicalHubRecord,
    ManualHubOverrideRecord,
    ManualOverrideCommand,
    ProviderHubCandidate,
)
from app.providers.rail_gtfs.schemas import GTFSStop
from app.providers.rail_gtfs.station_mapper import CanonicalHubIndex
from app.services.hub_override import HubOverrideIdentity, HubOverrideService
from app.services.hub_reconciliation import HubReconciliationIndex


async def _registry(session_factory):
    session = session_factory()
    city = City(
        name_zh="成都",
        province_name_zh="四川",
        adcode="510100",
        coordinate_system="GCJ02",
    )
    session.add(city)
    await session.flush()
    target = Hub(
        city_id=city.id,
        canonical_name_zh="成都东站",
        hub_type="RAILWAY",
        longitude=104.15,
        latitude=30.63,
        coordinate_system="GCJ02",
        active=True,
        passenger_service=True,
    )
    automatic = Hub(
        city_id=city.id,
        canonical_name_zh="成都南站",
        hub_type="RAILWAY",
        longitude=104.05,
        latitude=30.61,
        coordinate_system="GCJ02",
        active=True,
        passenger_service=True,
    )
    session.add_all([target, automatic])
    await session.commit()
    await session.close()
    return city, target, automatic


def _command(target_id, *, provider_id="stop-1", operator="alice", reason="verified"):
    return ManualOverrideCommand(
        provider="CHINA_RAILWAY_GTFS",
        provider_object_type="STOP",
        provider_hub_id=provider_id,
        canonical_hub_id=target_id,
        hub_type=HubType.RAILWAY,
        provider_city_name="成都",
        operator_identity=operator,
        reason=reason,
    )


def _candidate(name="成都南站", *, provider_id="stop-1"):
    return ProviderHubCandidate(
        provider="CHINA_RAILWAY_GTFS",
        provider_object_type="STOP",
        provider_hub_id=provider_id,
        hub_type=HubType.RAILWAY,
        name=name,
        city_name_zh="成都",
    )


@pytest.mark.asyncio
async def test_manual_override_wins_and_syncs_provider_ref(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        result = await HubOverrideService().apply(session, _command(target.id))
        await session.commit()
        override = await session.get(HubReconciliationOverride, result.override_id)
        ref = await session.scalar(
            select(HubProviderRef).where(HubProviderRef.provider_id == "stop-1")
        )
        assert override is not None
        assert ref is not None and ref.hub_id == target.id

        index = await HubReconciliationIndex.from_session(session, hub_types={HubType.RAILWAY})
        matched = index.reconcile(_candidate())
        assert matched.status == HubReconciliationStatus.MATCHED
        assert matched.match_strategy == "manual_override"
        assert matched.reason_code == HubReconciliationReasonCode.MANUAL_OVERRIDE
        assert matched.canonical_hub_id == target.id
        assert matched.manual_override_id == result.override_id


@pytest.mark.asyncio
async def test_revoke_restores_automatic_provider_ref_and_history(session_factory):
    _, target, automatic = await _registry(session_factory)
    async with session_factory() as session:
        session.add(
            HubProviderRef(
                hub_id=automatic.id,
                provider="CHINA_RAILWAY_GTFS",
                provider_object_type="STOP",
                provider_id="stop-2",
            )
        )
        await session.commit()
        service = HubOverrideService()
        await service.apply(session, _command(target.id, provider_id="stop-2"))
        await session.commit()
        await service.revoke(
            session,
            HubOverrideIdentity(
                provider="CHINA_RAILWAY_GTFS",
                provider_object_type="STOP",
                provider_hub_id="stop-2",
            ),
            operator_identity="bob",
            reason="source corrected",
        )
        await session.commit()
        ref = await session.scalar(
            select(HubProviderRef).where(HubProviderRef.provider_id == "stop-2")
        )
        assert ref is not None and ref.hub_id == automatic.id
        history = await service.history(
            session,
            HubOverrideIdentity(
                provider="CHINA_RAILWAY_GTFS",
                provider_object_type="STOP",
                provider_hub_id="stop-2",
            ),
        )
        assert [item.action.value for item in history] == ["APPLY", "REVOKE"]
        assert (
            await session.scalar(select(func.count()).select_from(HubReconciliationOverrideEvent))
            == 2
        )


@pytest.mark.asyncio
async def test_replacement_keeps_previous_mapping_and_only_one_active(session_factory):
    _, target, automatic = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        first = await service.apply(session, _command(target.id))
        replacement = await service.apply(session, _command(automatic.id), replace=True)
        await session.commit()
        assert replacement.previous_override_id == first.override_id
        active = list(
            (
                await session.scalars(
                    select(HubReconciliationOverride).where(
                        HubReconciliationOverride.provider_hub_id == "stop-1",
                        HubReconciliationOverride.status == "ACTIVE",
                    )
                )
            ).all()
        )
        assert len(active) == 1 and active[0].canonical_hub_id == automatic.id
        old = await session.get(HubReconciliationOverride, first.override_id)
        assert old is not None and old.status == "REPLACED"


@pytest.mark.asyncio
async def test_invalid_target_does_not_silently_fall_through(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        stored_target = await session.get(Hub, target.id)
        assert stored_target is not None
        stored_target.active = False
        await session.commit()
        service = HubOverrideService()
        with pytest.raises(AppError) as error:
            await service.apply(session, _command(target.id))
        assert error.value.code == "OVERRIDE_TARGET_INACTIVE"


def test_command_rejects_unsafe_operator_and_reason():
    with pytest.raises(ValueError):
        _command(uuid4(), operator="alice\nforged", reason="ok")
    with pytest.raises(ValueError):
        _command(uuid4(), reason="   ")


def test_manual_override_is_provider_and_object_type_specific():
    from app.domain.normalization import normalize_hub_name
    from app.domain.reconciliation import CanonicalHubRecord

    target = uuid4()
    record = CanonicalHubRecord(
        hub_id=target,
        city_id=uuid4(),
        city_name="成都",
        canonical_name="成都东站",
        normalized_name=normalize_hub_name("成都东站", HubType.RAILWAY),
        aliases=(),
        hub_type=HubType.RAILWAY,
    )
    override = ManualHubOverrideRecord(
        override_id=uuid4(),
        provider="P1",
        provider_object_type="STOP",
        provider_hub_id="same-id",
        canonical_hub_id=target,
        hub_type=HubType.RAILWAY,
    )
    index = HubReconciliationIndex(
        [record],
        {},
        {("P1", "STOP", "same-id"): (override,)},
    )
    assert (
        index.reconcile(
            ProviderHubCandidate(
                provider="P1",
                provider_object_type="STOP",
                provider_hub_id="same-id",
                hub_type=HubType.RAILWAY,
                name="未知站",
            )
        ).canonical_hub_id
        == target
    )
    other_type = index.reconcile(
        ProviderHubCandidate(
            provider="P1",
            provider_object_type="POI",
            provider_hub_id="same-id",
            hub_type=HubType.RAILWAY,
            name="未知站",
        )
    )
    assert other_type.reason_code == HubReconciliationReasonCode.NO_MATCH


def test_invalid_manual_override_does_not_fall_through_to_name_match():
    from app.domain.normalization import normalize_hub_name
    from app.domain.reconciliation import CanonicalHubRecord

    target = uuid4()
    record = CanonicalHubRecord(
        hub_id=target,
        city_id=uuid4(),
        city_name="成都",
        canonical_name="成都东站",
        normalized_name=normalize_hub_name("成都东站", HubType.RAILWAY),
        aliases=(),
        hub_type=HubType.RAILWAY,
        active=False,
    )
    override = ManualHubOverrideRecord(
        override_id=uuid4(),
        provider="P1",
        provider_object_type="STOP",
        provider_hub_id="stop-1",
        canonical_hub_id=target,
        hub_type=HubType.RAILWAY,
    )
    result = HubReconciliationIndex(
        [record], {}, {("P1", "STOP", "stop-1"): (override,)}
    ).reconcile(
        ProviderHubCandidate(
            provider="P1",
            provider_object_type="STOP",
            provider_hub_id="stop-1",
            hub_type=HubType.RAILWAY,
            name="成都东站",
            city_name_zh="成都",
        )
    )
    assert result.status == HubReconciliationStatus.UNRESOLVED
    assert result.reason_code == HubReconciliationReasonCode.MANUAL_OVERRIDE_TARGET_INACTIVE


def test_manual_override_resolves_ambiguous_name():
    from app.domain.normalization import normalize_hub_name

    first = CanonicalHubRecord(
        hub_id=uuid4(),
        city_id=uuid4(),
        city_name="成都",
        canonical_name="同名站",
        normalized_name=normalize_hub_name("同名站", HubType.RAILWAY),
        aliases=(),
    )
    second = CanonicalHubRecord(
        hub_id=uuid4(),
        city_id=uuid4(),
        city_name="乐山",
        canonical_name="同名站",
        normalized_name=normalize_hub_name("同名站", HubType.RAILWAY),
        aliases=(),
    )
    override = ManualHubOverrideRecord(
        override_id=uuid4(),
        provider="P1",
        provider_object_type="STOP",
        provider_hub_id="ambiguous-stop",
        canonical_hub_id=first.hub_id,
        hub_type=HubType.RAILWAY,
    )
    result = HubReconciliationIndex(
        [first, second], {}, {("P1", "STOP", "ambiguous-stop"): (override,)}
    ).reconcile(
        ProviderHubCandidate(
            provider="P1",
            provider_object_type="STOP",
            provider_hub_id="ambiguous-stop",
            hub_type=HubType.RAILWAY,
            name="同名站",
        )
    )
    assert result.status == HubReconciliationStatus.MATCHED
    assert result.canonical_hub_id == first.hub_id


def test_manual_override_resolves_unknown_name():
    from app.domain.normalization import normalize_hub_name

    record = CanonicalHubRecord(
        hub_id=uuid4(),
        city_id=uuid4(),
        city_name="成都",
        canonical_name="成都东站",
        normalized_name=normalize_hub_name("成都东站", HubType.RAILWAY),
        aliases=(),
    )
    override = ManualHubOverrideRecord(
        override_id=uuid4(),
        provider="P1",
        provider_object_type="STOP",
        provider_hub_id="unknown-stop",
        canonical_hub_id=record.hub_id,
        hub_type=HubType.RAILWAY,
    )
    result = HubReconciliationIndex(
        [record], {}, {("P1", "STOP", "unknown-stop"): (override,)}
    ).reconcile(
        ProviderHubCandidate(
            provider="P1",
            provider_object_type="STOP",
            provider_hub_id="unknown-stop",
            hub_type=HubType.RAILWAY,
            name="完全未知站",
        )
    )
    assert result.status == HubReconciliationStatus.MATCHED
    assert result.reason_code == HubReconciliationReasonCode.MANUAL_OVERRIDE


@pytest.mark.asyncio
async def test_override_rejects_type_and_city_conflicts(session_factory):
    city, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        with pytest.raises(AppError) as type_error:
            await service.apply(
                session,
                _command(target.id).model_copy(update={"hub_type": HubType.AIRPORT}),
            )
        assert type_error.value.code == "OVERRIDE_TARGET_TYPE_CONFLICT"
        with pytest.raises(AppError) as city_error:
            await service.apply(
                session,
                _command(target.id).model_copy(update={"provider_city_name": "乐山"}),
            )
        assert city_error.value.code == "OVERRIDE_TARGET_CITY_CONFLICT"
        assert (
            await session.scalar(select(func.count()).select_from(HubReconciliationOverride)) == 0
        )


@pytest.mark.asyncio
async def test_override_rejects_missing_and_non_passenger_targets(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        with pytest.raises(AppError) as missing:
            await service.apply(session, _command(uuid4()))
        assert missing.value.code == "OVERRIDE_TARGET_NOT_FOUND"
        stored_target = await session.get(Hub, target.id)
        assert stored_target is not None
        stored_target.passenger_service = False
        await session.commit()
        with pytest.raises(AppError) as unavailable:
            await service.apply(session, _command(target.id))
        assert unavailable.value.code == "OVERRIDE_TARGET_NOT_PASSENGER"


@pytest.mark.asyncio
async def test_second_apply_requires_explicit_replace(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        await service.apply(session, _command(target.id))
        with pytest.raises(AppError) as error:
            await service.apply(session, _command(target.id))
        assert error.value.code == "OVERRIDE_ALREADY_ACTIVE"


@pytest.mark.asyncio
async def test_database_unique_index_blocks_second_active_row(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        await HubOverrideService().apply(session, _command(target.id))
        await session.commit()
        session.add(
            HubReconciliationOverride(
                provider="CHINA_RAILWAY_GTFS",
                provider_object_type="STOP",
                provider_hub_id="stop-1",
                hub_type="RAILWAY",
                canonical_hub_id=target.id,
                operator_identity="race",
                reason="concurrent attempt",
            )
        )
        with pytest.raises(IntegrityError):
            await session.flush()
        await session.rollback()


@pytest.mark.asyncio
async def test_replace_history_records_previous_target(session_factory):
    _, target, automatic = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        await service.apply(session, _command(target.id))
        replacement = await service.apply(session, _command(automatic.id), replace=True)
        await session.commit()
        history = await service.history(
            session,
            HubOverrideIdentity(
                provider="CHINA_RAILWAY_GTFS",
                provider_object_type="STOP",
                provider_hub_id="stop-1",
            ),
        )
        assert history[-1].action.value == "REPLACE"
        assert history[-1].previous_override_id is not None
        assert history[-1].canonical_hub_id == automatic.id
        assert replacement.previous_override_id == history[-1].previous_override_id


@pytest.mark.asyncio
async def test_provider_ref_conflict_is_visible(session_factory):
    _, target, automatic = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        await service.apply(session, _command(target.id))
        await session.commit()
        ref = await session.scalar(
            select(HubProviderRef).where(HubProviderRef.provider_id == "stop-1")
        )
        assert ref is not None
        ref.hub_id = automatic.id
        await session.commit()
        index = await HubReconciliationIndex.from_session(session, hub_types={HubType.RAILWAY})
        result = index.reconcile(_candidate())
        assert result.status == HubReconciliationStatus.UNRESOLVED
        assert (
            result.reason_code == HubReconciliationReasonCode.MANUAL_OVERRIDE_PROVIDER_REF_CONFLICT
        )


@pytest.mark.asyncio
async def test_revoked_override_is_no_longer_loaded(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        service = HubOverrideService()
        await service.apply(session, _command(target.id))
        await session.commit()
        await service.revoke(
            session,
            HubOverrideIdentity(
                provider="CHINA_RAILWAY_GTFS",
                provider_object_type="STOP",
                provider_hub_id="stop-1",
            ),
            operator_identity="bob",
            reason="撤销测试",
        )
        await session.commit()
        index = await HubReconciliationIndex.from_session(session, hub_types={HubType.RAILWAY})
        result = index.reconcile(_candidate(name="未知站"))
        assert result.reason_code == HubReconciliationReasonCode.NO_MATCH


@pytest.mark.asyncio
async def test_gtfs_station_mapper_consumes_manual_override(session_factory):
    _, target, _ = await _registry(session_factory)
    async with session_factory() as session:
        await HubOverrideService().apply(session, _command(target.id))
        await session.commit()
        mapper = await CanonicalHubIndex.from_session(session)
        match = mapper.reconcile(GTFSStop("stop-1", "完全不同的站", city_name="成都"))
        assert match.status == "matched"
        assert match.hub_id == target.id
        assert match.reason == "manual_override"


def test_amap_candidate_uses_the_same_manual_boundary():
    from app.domain.normalization import normalize_hub_name

    record = CanonicalHubRecord(
        hub_id=uuid4(),
        city_id=uuid4(),
        city_name="成都",
        canonical_name="成都天府国际机场",
        normalized_name=normalize_hub_name("成都天府国际机场", HubType.AIRPORT),
        aliases=("天府机场",),
        hub_type=HubType.AIRPORT,
    )
    override = ManualHubOverrideRecord(
        override_id=uuid4(),
        provider="AMAP",
        provider_object_type="POI",
        provider_hub_id="amap-poi-1",
        canonical_hub_id=record.hub_id,
        hub_type=HubType.AIRPORT,
    )
    candidate = HubCandidate(
        canonical_name_zh="一个未收录别名",
        city_name_zh="成都",
        hub_type=HubType.AIRPORT,
        coordinate=Coordinate(longitude=104.4, latitude=30.3, coordinate_system="GCJ02"),
        source="amap:poi",
        provider_reference=ProviderReference(
            provider="AMAP", provider_object_type="POI", provider_id="amap-poi-1"
        ),
    )
    result = HubReconciliationIndex(
        [record], {}, {("AMAP", "POI", "amap-poi-1"): (override,)}
    ).reconcile(candidate)
    assert result.canonical_hub_id == record.hub_id
    assert result.reason_code == HubReconciliationReasonCode.MANUAL_OVERRIDE
