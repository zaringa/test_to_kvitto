import pytest

from app.domain import build_schedule, calculate_amount


@pytest.mark.parametrize("price", [990_000, 1_990_000, 2_990_000])
def test_discount_is_integer_kopecks(price):
    amount, discount = calculate_amount(price, "KVITTO10")
    assert amount == price * 9 // 10
    assert discount == price // 10
    assert type(amount) is int
    assert type(discount) is int
    assert calculate_amount(price, None) == (price, 0)


@pytest.mark.parametrize("amount", [990_000, 1_990_000, 2_990_000, 891_000, 1_791_000, 2_691_000])
@pytest.mark.parametrize("months", [3, 6, 12])
def test_schedule_preserves_every_kopeck(amount, months):
    schedule = build_schedule(amount, months)
    assert len(schedule) == months
    assert sum(schedule) == amount
    assert all(type(payment) is int for payment in schedule)
    assert max(schedule) - min(schedule) <= 1
    assert schedule == sorted(schedule, reverse=True)


def test_schedule_matches_assignment_example():
    assert build_schedule(1_990_000, 3) == [663_334, 663_333, 663_333]


@pytest.mark.parametrize("amount,months", [(0, 3), (-1, 3), (100, 0), (100, 4)])
def test_invalid_schedule_is_rejected(amount, months):
    with pytest.raises(ValueError):
        build_schedule(amount, months)
