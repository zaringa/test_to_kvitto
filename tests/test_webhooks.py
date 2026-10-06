import asyncio
import hashlib
import hmac
import json
from uuid import uuid4

import httpx
import pytest

from app.main import create_app

STATUSES = ["pending", "succeeded", "failed", "refunded"]
VALID_TRANSITIONS = {("pending", "succeeded"), ("pending", "failed"), ("succeeded", "refunded")}


async def change_status(client, payment_id, status):
    return await client.post("/webhooks/bank", json={"payment_id": payment_id, "status": status})


@pytest.mark.parametrize("current", STATUSES)
@pytest.mark.parametrize("target", STATUSES)
async def test_all_status_transitions(client, payment_data, current, target):
    created = await client.post("/payments", json=payment_data)
    assert created.status_code == 201
    payment_id = created.json()["id"]
    if current in {"succeeded", "refunded"}:
        assert (await change_status(client, payment_id, "succeeded")).status_code == 200
    if current in {"failed", "refunded"}:
        assert (await change_status(client, payment_id, current)).status_code == 200

    response = await change_status(client, payment_id, target)
    allowed = (current, target) in VALID_TRANSITIONS
    assert response.status_code == (200 if allowed else 409)
    assert response.json() == ({"result": "ok"} if allowed else {"error": "invalid_transition"})
    fetched = await client.get(f"/payments/{payment_id}")
    assert fetched.json()["status"] == (target if allowed else current)


async def test_missing_webhook_payment(client):
    response = await change_status(client, str(uuid4()), "succeeded")
    assert response.status_code == 404
    assert response.json() == {"detail": "payment_not_found"}


@pytest.mark.parametrize(
    "payload",
    [
        {"payment_id": "not-a-uuid", "status": "succeeded"},
        {"payment_id": str(uuid4()), "status": "unknown"},
        {"payment_id": str(uuid4())},
    ],
)
async def test_webhook_validation(client, payload):
    response = await client.post("/webhooks/bank", json=payload)
    assert response.status_code == 422
    assert isinstance(response.json()["detail"], list)


async def test_concurrent_webhooks_apply_only_one_transition(client, payment_data):
    created = await client.post("/payments", json=payment_data)
    payment_id = created.json()["id"]
    responses = await asyncio.gather(
        change_status(client, payment_id, "succeeded"),
        change_status(client, payment_id, "failed"),
    )
    assert sorted(response.status_code for response in responses) == [200, 409]
    winner = "succeeded" if responses[0].status_code == 200 else "failed"
    fetched = await client.get(f"/payments/{payment_id}")
    assert fetched.json()["status"] == winner


async def test_hmac_signature_and_unchanged_status_on_failure(system, payment_data):
    created = await system.client.post("/payments", json=payment_data)
    payment_id = created.json()["id"]
    settings = system.settings.model_copy(update={"webhook_secret": "test-bank-secret"})
    app = create_app(settings)
    body = json.dumps({"payment_id": payment_id, "status": "succeeded"}).encode()
    signature = hmac.new(settings.webhook_secret.encode(), body, hashlib.sha256).hexdigest()
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            for bad_signature in (None, "0" * 64):
                headers = {"Content-Type": "application/json"}
                if bad_signature is not None:
                    headers["X-Signature"] = bad_signature
                response = await client.post("/webhooks/bank", content=body, headers=headers)
                assert response.status_code == 401
                assert response.json() == {"detail": "invalid_signature"}
                fetched = await system.client.get(f"/payments/{payment_id}")
                assert fetched.json()["status"] == "pending"

            response = await client.post(
                "/webhooks/bank",
                content=body,
                headers={
                    "Content-Type": "application/json",
                    "X-Signature": signature,
                },
            )
            assert response.status_code == 200
            assert response.json() == {"result": "ok"}
            fetched = await system.client.get(f"/payments/{payment_id}")
            assert fetched.json()["status"] == "succeeded"
