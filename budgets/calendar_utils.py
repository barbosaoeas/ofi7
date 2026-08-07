from datetime import date, datetime, time as dt_time, timedelta
from decimal import Decimal
from typing import Optional, Tuple

from django.utils import timezone

KANBAN_CUTOFF_TIME = dt_time(17, 48)

WORKDAY_WEEKDAYS = (0, 1, 2, 3, 4, 5)


def get_local_today() -> date:
    return timezone.localdate()


def get_local_now() -> datetime:
    return timezone.localtime(timezone.now())


def next_weekday_including_saturday(from_date: date) -> date:
    cursor = from_date + timedelta(days=1)
    while cursor.weekday() not in WORKDAY_WEEKDAYS:
        cursor += timedelta(days=1)
    return cursor


def capped_work_delta_seconds(
    last_started_at: Optional[datetime],
    now: datetime,
    allow_overtime: bool,
) -> Tuple[int, Optional[datetime]]:
    if last_started_at is None:
        return 0, None

    last_local = timezone.localtime(last_started_at)
    now_local = timezone.localtime(now)

    if allow_overtime:
        effective_end = now_local
    else:
        tz = timezone.get_current_timezone()
        started_day = last_local.date()
        cutoff_dt = timezone.make_aware(
            datetime.combine(started_day, KANBAN_CUTOFF_TIME), tz
        )
        if last_local >= cutoff_dt:
            effective_end = last_local
        elif now_local.date() == started_day:
            effective_end = now_local if now_local <= cutoff_dt else cutoff_dt
        else:
            effective_end = cutoff_dt

    delta = int((effective_end - last_local).total_seconds())
    return max(delta, 0), effective_end


SECONDS_PER_HOUR = Decimal(3600)


def seconds_to_hours_decimal(seconds: int) -> Decimal:
    if seconds <= 0:
        return Decimal("0.00")
    return (Decimal(seconds) / SECONDS_PER_HOUR).quantize(Decimal("0.01"))
