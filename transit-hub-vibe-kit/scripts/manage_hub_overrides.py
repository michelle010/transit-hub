#!/usr/bin/env python3
"""Apply and inspect audited manual canonical-hub reconciliation overrides.

This is an operator CLI, not a public API.  It requires stable provider and
canonical IDs; operator identity is recorded for audit purposes but is not an
authentication mechanism.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy.exc import SQLAlchemyError

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPOSITORY_ROOT / "apps" / "api"))

from app.core.errors import AppError  # noqa: E402
from app.db.models import City, Hub  # noqa: E402
from app.db.session import SessionFactory, engine  # noqa: E402
from app.domain.enums import HubType  # noqa: E402
from app.domain.reconciliation import ManualOverrideCommand  # noqa: E402
from app.services.hub_override import (  # noqa: E402
    HubOverrideIdentity,
    HubOverrideService,
)


def _identity_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--provider", required=True)
    parser.add_argument("--provider-object-type", required=True)
    parser.add_argument("--provider-hub-id", required=True)


def _json_flag(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--json", action="store_true", default=argparse.SUPPRESS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--json", action="store_true", help="Emit machine-readable JSON")
    commands = parser.add_subparsers(dest="command", required=True)

    apply = commands.add_parser("apply", help="Apply a new manual override")
    _json_flag(apply)
    apply.add_argument("--provider", required=True)
    apply.add_argument("--provider-object-type", required=True)
    apply.add_argument("--provider-hub-id", required=True)
    apply.add_argument("--canonical-hub-id", required=True, type=UUID)
    apply.add_argument("--hub-type", required=True, choices=[item.value for item in HubType])
    apply.add_argument("--provider-city-id", type=UUID)
    apply.add_argument("--provider-city-name")
    apply.add_argument("--operator", required=True, dest="operator_identity")
    apply.add_argument("--reason", required=True)
    apply.add_argument("--replace", action="store_true")

    for name, help_text in (
        ("inspect", "Show the active override"),
        ("history", "Show immutable correction history"),
    ):
        command = commands.add_parser(name, help=help_text)
        _json_flag(command)
        _identity_arguments(command)

    revoke = commands.add_parser("revoke", help="Revoke the active override")
    _json_flag(revoke)
    _identity_arguments(revoke)
    revoke.add_argument("--operator", required=True, dest="operator_identity")
    revoke.add_argument("--reason", required=True)
    return parser.parse_args(argv)


async def run(args: argparse.Namespace) -> int:
    service = HubOverrideService()
    try:
        async with SessionFactory() as session:
            if args.command == "apply":
                result = await service.apply(
                    session,
                    ManualOverrideCommand(
                        provider=args.provider,
                        provider_object_type=args.provider_object_type,
                        provider_hub_id=args.provider_hub_id,
                        canonical_hub_id=args.canonical_hub_id,
                        hub_type=HubType(args.hub_type),
                        provider_city_id=args.provider_city_id,
                        provider_city_name=args.provider_city_name,
                        operator_identity=args.operator_identity,
                        reason=args.reason,
                    ),
                    replace=args.replace,
                )
                payload = await _operation_payload(session, result)
            elif args.command == "revoke":
                identity = _identity(args)
                result = await service.revoke(
                    session,
                    identity,
                    operator_identity=args.operator_identity,
                    reason=args.reason,
                )
                payload = result.model_dump(mode="json")
            else:
                identity = _identity(args)
                if args.command == "inspect":
                    override = await service.inspect(session, identity)
                    payload = _override_payload(override)
                else:
                    payload = {
                        "identity": identity.model_dump(mode="json"),
                        "history": [
                            item.model_dump(mode="json")
                            for item in await service.history(session, identity)
                        ],
                    }
            _print_payload(payload, json_output=bool(getattr(args, "json", False)))
        return 0
    except (AppError, ValueError) as exc:
        payload = {
            "error": {
                "code": getattr(exc, "code", "VALIDATION_ERROR"),
                "message": getattr(exc, "message", str(exc)),
            }
        }
        _print_payload(payload, json_output=bool(getattr(args, "json", False)), error=True)
        return 2
    except SQLAlchemyError:
        _print_payload(
            {
                "error": {
                    "code": "DATABASE_UNAVAILABLE",
                    "message": "The manual hub correction database is temporarily unavailable.",
                }
            },
            json_output=bool(getattr(args, "json", False)),
            error=True,
        )
        return 2
    finally:
        await engine.dispose()


async def _operation_payload(session, result) -> dict[str, Any]:  # type: ignore[no-untyped-def]
    payload = result.model_dump(mode="json")
    hub = await session.get(Hub, result.canonical_hub_id) if result.canonical_hub_id else None
    if hub is not None:
        city = await session.get(City, hub.city_id)
        payload["canonical_context"] = {
            "name": hub.canonical_name_zh,
            "city": city.name_zh if city else None,
            "hub_type": hub.hub_type,
        }
    return payload


def _identity(args: argparse.Namespace) -> HubOverrideIdentity:
    return HubOverrideIdentity(
        provider=args.provider,
        provider_object_type=args.provider_object_type,
        provider_hub_id=args.provider_hub_id,
    )


def _override_payload(override) -> dict[str, Any] | None:  # type: ignore[no-untyped-def]
    if override is None:
        return None
    return {
        "override_id": str(override.id),
        "provider": override.provider,
        "provider_object_type": override.provider_object_type,
        "provider_hub_id": override.provider_hub_id,
        "canonical_hub_id": str(override.canonical_hub_id),
        "hub_type": override.hub_type,
        "status": override.status,
        "operator_identity": override.operator_identity,
        "reason": override.reason,
        "created_at": override.created_at.isoformat() if override.created_at else None,
        "updated_at": override.updated_at.isoformat() if override.updated_at else None,
    }


def _print_payload(
    payload: dict[str, Any] | None, *, json_output: bool, error: bool = False
) -> None:
    rendered = json.dumps(
        payload, ensure_ascii=False, indent=None if json_output else 2, default=str
    )
    print(rendered, file=sys.stderr if error else sys.stdout)


if __name__ == "__main__":
    arguments = parse_args()
    raise SystemExit(asyncio.run(run(arguments)))
