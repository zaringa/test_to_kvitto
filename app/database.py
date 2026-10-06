from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.models import Base, Tariff

TARIFFS = [
    {"id": "basic", "title": "Basic", "price": 990_000},
    {"id": "standard", "title": "Standard", "price": 1_990_000},
    {"id": "premium", "title": "Premium", "price": 2_990_000},
]


def make_engine(database_url: str) -> AsyncEngine:
    return create_async_engine(database_url, pool_pre_ping=True)


async def initialize_database(engine: AsyncEngine) -> None:
    # Для тестового задания create_all заменяет необязательные миграции.
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(insert(Tariff).values(TARIFFS).on_conflict_do_nothing())
