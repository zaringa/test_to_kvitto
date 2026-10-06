import asyncio
from datetime import datetime
from uuid import UUID, uuid4

import pytest
from sqlalchemy import func, select

from app.models import Payment


async def test_tariffs_are_seeded(client):
    response = await client.get("/tariffs")
    assert response.status_code == 200
    assert response.json() == [
        {"id": "basic", "title": "Basic", "price": 990_000},
        {"id": "standard", "title": "Standard", "price": 1_990_000},
        {"id": "premium", "title": "Premium", "price": 2_990_000},
    ]


async def test_health(client):
    response = await client.get("/health")
    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


@pytest.mark.parametrize(
    "promo_code,amount,discount",
    [
        (None, 1_990_000, 0),
        ("KVITTO10", 1_791_000, 199_000),
        ("kvitto10", 1_791_000, 199_000),
        ("KvItTo10", 1_791_000, 199_000),
    ],
)
async def test_payment_amount_and_response(client, payment_data, promo_code, amount, discount):
    response = await client.post("/payments", json={**payment_data, "promo_code": promo_code})
    assert response.status_code == 201
    payment = response.json()
    UUID(payment["id"])
    assert datetime.fromisoformat(payment["created_at"]).tzinfo is not None
    assert payment == {
        "id": payment["id"],
        "status": "pending",
        "tariff_id": "standard",
        "amount": amount,
        "discount": discount,
        "method": "card",
        "installment_months": None,
        "schedule": None,
        "email": "student@example.com",
        "created_at": payment["created_at"],
    }
    fetched = await client.get(f"/payments/{payment['id']}")
    assert fetched.status_code == 200
    assert fetched.json() == payment


@pytest.mark.parametrize("method", ["card", "sbp"])
async def test_payment_methods_without_schedule(client, payment_data, method):
    response = await client.post("/payments", json={**payment_data, "method": method})
    assert response.status_code == 201
    assert response.json()["method"] == method
    assert response.json()["schedule"] is None
    assert response.json()["installment_months"] is None


@pytest.mark.parametrize("months", [3, 6, 12])
@pytest.mark.parametrize("promo", [None, "kvitto10"])
async def test_installment_schedule(client, payment_data, months, promo):
    response = await client.post(
        "/payments",
        json={
            **payment_data,
            "method": "installment",
            "installment_months": months,
            "promo_code": promo,
        },
    )
    assert response.status_code == 201
    payment = response.json()
    assert payment["installment_months"] == months
    assert payment["method"] == "installment"
    assert payment["amount"] == (1_990_000 if promo is None else 1_791_000)
    assert len(payment["schedule"]) == months
    assert sum(payment["schedule"]) == payment["amount"]
    assert payment["schedule"] == sorted(payment["schedule"], reverse=True)
    if months == 3 and promo is None:
        assert payment["schedule"] == [663_334, 663_333, 663_333]


@pytest.mark.parametrize(
    "changes",
    [
        {"promo_code": "UNKNOWN"},
        {"promo_code": ""},
        {"email": "not-an-email"},
        {"method": "cash"},
        {"method": "installment"},
        {"method": "installment", "installment_months": 4},
        {"method": "installment", "installment_months": 3.0},
        {"method": "installment", "installment_months": True},
        {"method": "installment", "installment_months": "3"},
        {"installment_months": 3},
        {"amount": 1},
        {"tariff_id": ""},
    ],
)
async def test_payment_validation_uses_fastapi_format(client, payment_data, changes):
    response = await client.post("/payments", json={**payment_data, **changes})
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)
    assert response.json()["detail"][0]["loc"][0] == "body"


async def test_unknown_tariff(client, payment_data):
    response = await client.post("/payments", json={**payment_data, "tariff_id": "unknown"})
    assert response.status_code == 404
    assert response.json() == {"detail": "tariff_not_found"}


async def test_missing_payment(client):
    response = await client.get(f"/payments/{uuid4()}")
    assert response.status_code == 404
    assert response.json() == {"detail": "payment_not_found"}


async def test_invalid_payment_id(client):
    response = await client.get("/payments/not-a-uuid")
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


async def test_idempotency_returns_existing_payment(client, payment_data, system):
    key = uuid4().hex
    first = await client.post("/payments", json=payment_data, headers={"Idempotency-Key": key})
    second = await client.post(
        "/payments",
        json={**payment_data, "tariff_id": "premium", "email": "other@example.com"},
        headers={"Idempotency-Key": key},
    )
    assert first.status_code == 201
    assert second.status_code == 200
    assert first.json() == second.json()
    async with system.sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(Payment).where(Payment.idempotency_key == key)
        )
    assert count == 1


async def test_idempotency_survives_concurrent_requests(client, payment_data, system):
    key = uuid4().hex
    responses = await asyncio.gather(
        *(
            client.post("/payments", json=payment_data, headers={"Idempotency-Key": key})
            for _ in range(12)
        )
    )
    assert sorted(response.status_code for response in responses) == [200] * 11 + [201]
    assert len({response.json()["id"] for response in responses}) == 1
    async with system.sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(Payment).where(Payment.idempotency_key == key)
        )
    assert count == 1


async def test_requests_without_key_create_different_payments(client, payment_data):
    first = await client.post("/payments", json=payment_data)
    second = await client.post("/payments", json=payment_data)
    assert first.status_code == second.status_code == 201
    assert first.json()["id"] != second.json()["id"]


@pytest.mark.parametrize("key", ["", "   ", "x" * 201])
async def test_invalid_idempotency_key(client, payment_data, key):
    response = await client.post("/payments", json=payment_data, headers={"Idempotency-Key": key})
    assert response.status_code == 422
    assert response.json()["detail"][0]["loc"][0] == "header"
