import asyncio
import logging
import socket
from uuid import uuid4

from pydantic import ValidationError
from redis.exceptions import ResponseError
from sqlalchemy import text
from sqlalchemy.ext.asyncio import async_sessionmaker

from app.broker import RedisBroker, make_redis
from app.config import Settings
from app.database import initialize_database, make_engine
from app.schemas import Command
from app.service import execute_command

logger = logging.getLogger(__name__)


class Worker:
    def __init__(self, broker: RedisBroker, sessions: async_sessionmaker):
        self.broker = broker
        self.sessions = sessions
        self.consumer = f"{socket.gethostname()}-{uuid4().hex[:8]}"
        self.reclaim_cursor = "0-0"

    async def ensure_group(self) -> None:
        try:
            await self.broker.redis.xgroup_create(
                self.broker.stream, self.broker.group, id="0", mkstream=True
            )
        except ResponseError as error:
            if "BUSYGROUP" not in str(error):
                raise

    async def heartbeat(self) -> None:
        # Готовность означает, что worker может подключиться и к БД, и к Redis.
        async with self.sessions() as session:
            await session.execute(text("SELECT 1"))
        await self.broker.redis.set(
            self.broker.heartbeat_key, "ready", ex=self.broker.settings.heartbeat_ttl_seconds
        )

    async def process_message(self, message_id: str, fields: dict[str, str]) -> None:
        try:
            command = Command.model_validate_json(fields["command"])
        except (KeyError, ValidationError):
            logger.error("Invalid command envelope; message_id=%s", message_id)
            async with self.broker.redis.pipeline(transaction=True) as pipeline:
                pipeline.xack(self.broker.stream, self.broker.group, message_id)
                pipeline.xdel(self.broker.stream, message_id)
                await pipeline.execute()
            return
        result = await execute_command(self.sessions, command)
        # Сначала коммит PostgreSQL; при сбое Redis повтор получит сохранённый результат.
        await self.broker.publish_result(message_id, command, result)

    async def run(self) -> None:
        await self.ensure_group()
        logger.info("Worker started; consumer=%s", self.consumer)
        while True:
            try:
                await self.heartbeat()
                claimed = await self.broker.redis.xautoclaim(
                    self.broker.stream,
                    self.broker.group,
                    self.consumer,
                    min_idle_time=self.broker.settings.reclaim_idle_ms,
                    start_id=self.reclaim_cursor,
                    count=1,
                )
                self.reclaim_cursor, messages = claimed[0], claimed[1]
                if not messages:
                    streams = await self.broker.redis.xreadgroup(
                        self.broker.group,
                        self.consumer,
                        {self.broker.stream: ">"},
                        count=1,
                        block=1000,
                    )
                    messages = streams[0][1] if streams else []
                for message_id, fields in messages:
                    await self.process_message(message_id, fields)
            except ResponseError as error:
                logger.exception("Redis command failed")
                if "NOGROUP" in str(error):
                    await self.ensure_group()
                await asyncio.sleep(1)
            except Exception:
                # Не подтверждаем сообщение при сбое: оно останется pending для повтора.
                logger.exception("Processing failed; pending command will be retried")
                await asyncio.sleep(1)


async def main() -> None:
    settings = Settings()
    engine = make_engine(settings.database_url)
    redis = make_redis(settings)
    try:
        await initialize_database(engine)
        sessions = async_sessionmaker(engine, expire_on_commit=False)
        await Worker(RedisBroker(redis, settings), sessions).run()
    finally:
        await redis.aclose()
        await engine.dispose()


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        pass
