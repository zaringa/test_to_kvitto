import asyncio
from uuid import UUID, uuid4

from redis.asyncio import Redis

from app.config import Settings
from app.schemas import Action, Command, CommandResult


def make_redis(settings: Settings) -> Redis:
    return Redis.from_url(
        settings.redis_url,
        decode_responses=True,
        socket_connect_timeout=2,
        socket_timeout=settings.request_timeout_seconds + 5,
    )


class RedisBroker:
    def __init__(self, redis: Redis, settings: Settings):
        self.redis = redis
        self.settings = settings
        self.stream = f"{settings.redis_prefix}:commands"
        self.group = "payment-workers"
        self.heartbeat_key = f"{settings.redis_prefix}:worker-ready"

    def reply_key(self, request_id: UUID) -> str:
        return f"{self.settings.redis_prefix}:reply:{request_id}"

    async def request(self, action: Action, payload: dict) -> CommandResult:
        command = Command(request_id=uuid4(), action=action, payload=payload)
        # Таймаут охватывает и отправку, и ожидание ответа.
        async with asyncio.timeout(self.settings.request_timeout_seconds + 2):
            await self.redis.xadd(self.stream, {"command": command.model_dump_json()})
            reply = await self.redis.blpop(
                self.reply_key(command.request_id), timeout=self.settings.request_timeout_seconds
            )
        if reply is None:
            raise TimeoutError("Worker did not respond in time")
        return CommandResult.model_validate_json(reply[1])

    async def publish_result(
        self, message_id: str, command: Command, result: CommandResult
    ) -> None:
        # Ответ, ACK и удаление обработанного сообщения выполняются атомарно в Redis.
        async with self.redis.pipeline(transaction=True) as pipeline:
            pipeline.rpush(self.reply_key(command.request_id), result.model_dump_json())
            pipeline.expire(self.reply_key(command.request_id), self.settings.reply_ttl_seconds)
            pipeline.xack(self.stream, self.group, message_id)
            pipeline.xdel(self.stream, message_id)
            await pipeline.execute()
