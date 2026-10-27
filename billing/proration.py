from dataclasses import dataclass
from datetime import date


@dataclass(frozen=True)
class Proration:
    days_total: int
    days_remaining: int
    credit_minor: int  # unused part of the old plan, given back
    charge_minor: int  # remaining part of the new plan, billed now
    net_minor: int  # charge - credit. Negative means the customer is owed money.


def _round_half_up_div(numerator: int, denominator: int) -> int:
    """Integer division rounding half up. No floats, so results never drift."""
    return (2 * numerator + denominator) // (2 * denominator)


def prorate_plan_change(
    old_price_minor: int,
    new_price_minor: int,
    period_start: date,
    period_end: date,
    change_on: date,
) -> Proration:
    """Prorate a mid-cycle plan change by whole days.

    The old plan is credited for the days not used. The new plan is charged for
    the same days. Both are rounded independently, half up, in minor units.
    """
    if old_price_minor < 0 or new_price_minor < 0:
        raise ValueError("prices must be zero or positive")
    days_total = (period_end - period_start).days
    if days_total <= 0:
        raise ValueError("period_end must be after period_start")
    if not (period_start <= change_on <= period_end):
        raise ValueError("change_on must fall inside the billing period")

    days_remaining = (period_end - change_on).days
    credit = _round_half_up_div(old_price_minor * days_remaining, days_total)
    charge = _round_half_up_div(new_price_minor * days_remaining, days_total)
    return Proration(
        days_total=days_total,
        days_remaining=days_remaining,
        credit_minor=credit,
        charge_minor=charge,
        net_minor=charge - credit,
    )
