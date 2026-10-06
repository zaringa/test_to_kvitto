import asyncio
from uuid import uuid4

import httpx
import pytest
from redis.exceptions import ConnectionError
from sqlalchemy import func, select

from app.broker import RedisBroker
from app.main import create_app
from app.models import Payment, ProcessedCommand
from app.schemas import Action, Command, CommandResult
from app.service import execute_command
from app.worker import Worker


async def test_command_redelivery_uses_committed_result(system, payment_data):
    broker = RedisBroker(system.redis, system.settings)
    email = f"{uuid4().hex}@example.com"
    command = Command(
        request_id=uuid4(),
        action=Action.CREATE_PAYMENT,
        payload={"payment": {**payment_data, "email": email}, "idempotency_key": None},
    )
    # БД уже закоммитила платёж, но ответ в Redis ещё не отправлен (сбой worker).
    original = await execute_command(system.sessions, command)
    await system.redis.xadd(broker.stream, {"command": command.model_dump_json()})
    reply = await system.redis.blpop(broker.reply_key(command.request_id), timeout=5)
    assert reply is not None
    assert CommandResult.model_validate_json(reply[1]) == original
    assert original.status_code == 201
    async with system.sessions() as session:
        count = await session.scalar(
            select(func.count()).select_from(Payment).where(Payment.email == email)
        )
    assert count == 1


async def test_pending_command_is_reclaimed_after_worker_failure(system, payment_data):
    settings = system.settings.model_copy(
        update={
            "redis_prefix": f"{system.settings.redis_prefix}-orphan",
            "reclaim_idle_ms": 100,
        }
    )
    broker = RedisBroker(system.redis, settings)
    worker = Worker(broker, system.sessions)
    await worker.ensure_group()
    command = Command(
        request_id=uuid4(),
        action=Action.CREATE_PAYMENT,
        payload={"payment": payment_data, "idempotency_key": None},
    )
    await system.redis.xadd(broker.stream, {"command": command.model_dump_json()})
    consumed = await system.redis.xreadgroup(
        broker.group,
        "crashed-worker",
        {broker.stream: ">"},
        count=1,
    )
    assert len(consumed[0][1]) == 1
    assert (await system.redis.xpending(broker.stream, broker.group))["pending"] == 1
    task = asyncio.create_task(worker.run())
    try:
        reply = await system.redis.blpop(broker.reply_key(command.request_id), timeout=5)
        assert reply is not None
        result = CommandResult.model_validate_json(reply[1])
        assert result.status_code == 201
        assert result.body["status"] == "pending"
        assert (await system.redis.xpending(broker.stream, broker.group))["pending"] == 0
    finally:
        task.cancel()
        await asyncio.gather(task, return_exceptions=True)


async def test_failed_command_rolls_back_its_deduplication_record(system):
    command = Command(
        request_id=uuid4(),
        action=Action.GET_PAYMENT,
        payload={"payment_id": "invalid"},
    )
    with pytest.raises(ValueError):
        await execute_command(system.sessions, command)
    async with system.sessions() as session:
        assert await session.get(ProcessedCommand, command.request_id) is None


async def test_timeout_without_worker_returns_504(system):
    settings = system.settings.model_copy(
        update={
            "redis_prefix": f"{system.settings.redis_prefix}-timeout",
            "request_timeout_seconds": 1,
        }
    )
    app = create_app(settings)
    async with app.router.lifespan_context(app):
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/tariffs")
            assert response.status_code == 504
            assert response.json() == {"detail": "worker_timeout"}
            health = await client.get("/health")
            assert health.status_code == 503
            assert health.json() == {"detail": "worker_unavailable"}


async def test_redis_failure_returns_503(system, monkeypatch):
    app = create_app(system.settings)

    async def unavailable(*args, **kwargs):
        raise ConnectionError("Redis is unavailable")

    async with app.router.lifespan_context(app):
        monkeypatch.setattr(app.state.broker, "request", unavailable)
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app), base_url="http://test"
        ) as client:
            response = await client.get("/tariffs")
            assert response.status_code == 503
            assert response.json() == {"detail": "broker_unavailable"}
