import asyncio
import os
from dataclasses import dataclass
from uuid import uuid4

import httpx
import pytest
import pytest_asyncio
from redis.asyncio import Redis
from sqlalchemy.engine import make_url
from sqlalchemy.ext.asyncio import AsyncEngine, async_sessionmaker

from app.broker import RedisBroker, make_redis
from app.config import Settings
from app.database import initialize_database, make_engine
from app.main import create_app
from app.models import Base
from app.worker import Worker


@dataclass
class TestSystem:
    __test__ = False

    client: httpx.AsyncClient
    settings: Settings
    engine: AsyncEngine
    redis: Redis
    sessions: async_sessionmaker


@pytest_asyncio.fixture(scope="session")
async def system():
    database_url = os.getenv(
        "TEST_DATABASE_URL", "postgresql+asyncpg://kvitto:kvitto@localhost:55432/kvitto_test"
    )
    # Тесты очищают только специально выделенную тестовую БД.
    if not (make_url(database_url).database or "").endswith("_test"):
        raise RuntimeError("TEST_DATABASE_URL must point to a database ending in _test")
    settings = Settings(
        _env_file=None,
        database_url=database_url,
        redis_url=os.getenv("TEST_REDIS_URL", "redis://localhost:56379/0"),
        redis_prefix=f"kvitto-test-{uuid4().hex}",
        webhook_secret="",
    )
    engine = make_engine(settings.database_url)
    redis = make_redis(settings)
    tasks = []
    try:
        async with engine.begin() as connection:
            await connection.run_sync(Base.metadata.drop_all)
        await initialize_database(engine)
        await redis.ping()
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        broker = RedisBroker(redis, settings)
        # Два обработчика нужны, чтобы действительно проверить гонки запросов.
        tasks = [asyncio.create_task(Worker(broker, sessions).run()) for _ in range(2)]
        async with asyncio.timeout(10):
            while not await redis.exists(broker.heartbeat_key):
                for task in tasks:
                    if task.done():
                        task.result()
                await asyncio.sleep(0.02)
        app = create_app(settings)
        async with app.router.lifespan_context(app):
            async with httpx.AsyncClient(
                transport=httpx.ASGITransport(app=app), base_url="http://test"
            ) as client:
                yield TestSystem(client, settings, engine, redis, sessions)
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
        keys = [key async for key in redis.scan_iter(f"{settings.redis_prefix}*")]
        if keys:
            await redis.delete(*keys)
        await redis.aclose()
        await engine.dispose()


@pytest.fixture
def client(system):
    return system.client


@pytest.fixture
def payment_data():
    return {"tariff_id": "standard", "email": "student@example.com", "method": "card"}
