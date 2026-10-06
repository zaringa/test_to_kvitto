import asyncio

from app.broker import RedisBroker, make_redis
from app.config import Settings


async def check() -> None:
    settings = Settings()
    redis = make_redis(settings)
    try:
        broker = RedisBroker(redis, settings)
        if not await redis.exists(broker.heartbeat_key):
            raise SystemExit(1)
    finally:
        await redis.aclose()


if __name__ == "__main__":
    asyncio.run(check())
