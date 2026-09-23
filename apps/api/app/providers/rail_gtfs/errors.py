from app.core.errors import AppError


class RailProviderError(AppError):
    """Stable base error for local timetable provider failures."""

    def __init__(
        self,
        message: str,
        *,
        code: str = "RAIL_PROVIDER_ERROR",
        status_code: int = 502,
        details: list[dict[str, object]] | None = None,
    ) -> None:
        super().__init__(code, message, status_code=status_code, details=details)


class RailDataOutOfRangeError(RailProviderError):
    def __init__(self, requested_date: object, start_date: object, end_date: object) -> None:
        super().__init__(
            f"The railway timetable does not cover service date {requested_date}.",
            code="RAIL_DATA_OUT_OF_RANGE",
            status_code=422,
            details=[
                {
                    "requested_date": str(requested_date),
                    "available_from": str(start_date),
                    "available_to": str(end_date),
                }
            ],
        )


class RailFeedFormatError(RailProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="RAIL_FEED_FORMAT_ERROR", status_code=422)


class RailStationReconciliationError(RailProviderError):
    def __init__(self, message: str) -> None:
        super().__init__(message, code="RAIL_STATION_RECONCILIATION_ERROR", status_code=422)
