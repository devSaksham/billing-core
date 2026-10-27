from datetime import date

import pytest

from billing.proration import prorate_plan_change

START = date(2026, 11, 1)
END = date(2026, 12, 1)  # 30 day cycle


def test_change_on_first_day_credits_and_charges_full_period():
    p = prorate_plan_change(3000, 6000, START, END, START)
    assert (p.days_total, p.days_remaining) == (30, 30)
    assert (p.credit_minor, p.charge_minor, p.net_minor) == (3000, 6000, 3000)


def test_change_on_last_day_is_zero():
    p = prorate_plan_change(3000, 6000, START, END, END)
    assert (p.credit_minor, p.charge_minor, p.net_minor) == (0, 0, 0)


def test_mid_cycle_upgrade():
    p = prorate_plan_change(3000, 6000, START, END, date(2026, 11, 16))
    assert p.days_remaining == 15
    assert (p.credit_minor, p.charge_minor, p.net_minor) == (1500, 3000, 1500)


def test_mid_cycle_downgrade_is_negative_net():
    p = prorate_plan_change(6000, 3000, START, END, date(2026, 11, 16))
    assert p.net_minor == -1500


def test_same_price_nets_to_zero():
    p = prorate_plan_change(4999, 4999, START, END, date(2026, 11, 10))
    assert p.net_minor == 0


@pytest.mark.parametrize(
    "price, remaining, total, expected",
    [
        (100, 1, 3, 33),  # 33.33 rounds down
        (100, 2, 3, 67),  # 66.67 rounds up
        (1, 1, 2, 1),  # exactly 0.5 rounds up
        (3, 1, 2, 2),  # exactly 1.5 rounds up
    ],
)
def test_rounding_is_half_up(price, remaining, total, expected):
    start = date(2026, 1, 1)
    end = date(2026, 1, 1 + total)
    change_on = date(2026, 1, 1 + total - remaining)
    p = prorate_plan_change(price, price, start, end, change_on)
    assert p.credit_minor == expected


def test_credit_never_exceeds_old_price_for_any_day():
    for day in range(0, 31):
        change_on = date(2026, 11, 1 + day) if day < 30 else END
        p = prorate_plan_change(999, 1999, START, END, change_on)
        assert 0 <= p.credit_minor <= 999
        assert 0 <= p.charge_minor <= 1999


def test_change_before_period_is_rejected():
    with pytest.raises(ValueError, match="inside the billing period"):
        prorate_plan_change(100, 200, START, END, date(2026, 10, 31))


def test_change_after_period_is_rejected():
    with pytest.raises(ValueError, match="inside the billing period"):
        prorate_plan_change(100, 200, START, END, date(2026, 12, 2))


def test_zero_length_period_is_rejected():
    with pytest.raises(ValueError, match="after period_start"):
        prorate_plan_change(100, 200, START, START, START)


def test_negative_price_is_rejected():
    with pytest.raises(ValueError, match="zero or positive"):
        prorate_plan_change(-1, 200, START, END, START)
