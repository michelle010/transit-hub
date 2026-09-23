"""FastAPI dependency exports for application services."""

from app.services.transfer_dependencies import (
    build_transfer_evaluation_service,
    get_transfer_evaluation_service,
)

__all__ = ["build_transfer_evaluation_service", "get_transfer_evaluation_service"]
