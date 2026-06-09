from datetime import datetime


def apply_first_success(
    now: datetime,
    sla_deadline_at: datetime,
    first_success_at: datetime | None,
    sla_breached: bool,
) -> tuple[datetime, bool]:
    if first_success_at is not None:
        return first_success_at, sla_breached

    breached = sla_breached or now > sla_deadline_at
    return now, breached


def deadline_passed_while_open(
    now: datetime,
    sla_deadline_at: datetime,
    first_success_at: datetime | None,
    sla_breached: bool,
) -> bool:
    if sla_breached or first_success_at is not None:
        return False
    return now > sla_deadline_at
