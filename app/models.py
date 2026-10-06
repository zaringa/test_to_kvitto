from datetime import datetime
from uuid import UUID, uuid4

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    DateTime,
    ForeignKey,
    SmallInteger,
    String,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


class Tariff(Base):
    __tablename__ = "tariffs"
    __table_args__ = (CheckConstraint("price > 0", name="tariff_price_positive"),)

    id: Mapped[str] = mapped_column(String(30), primary_key=True)
    title: Mapped[str] = mapped_column(String(100))
    price: Mapped[int] = mapped_column(BigInteger)


class Payment(Base):
    __tablename__ = "payments"
    __table_args__ = (
        CheckConstraint("amount > 0 AND discount >= 0", name="payment_amount_valid"),
        CheckConstraint(
            "status IN ('pending', 'succeeded', 'failed', 'refunded')",
            name="payment_status_valid",
        ),
        CheckConstraint("method IN ('card', 'sbp', 'installment')", name="payment_method_valid"),
        CheckConstraint(
            "(method = 'installment' AND installment_months IS NOT NULL "
            "AND installment_months IN (3, 6, 12) AND schedule IS NOT NULL) "
            "OR (method != 'installment' AND installment_months IS NULL AND schedule IS NULL)",
            name="payment_installment_valid",
        ),
    )

    id: Mapped[UUID] = mapped_column(primary_key=True, default=uuid4)
    status: Mapped[str] = mapped_column(String(20), default="pending")
    tariff_id: Mapped[str] = mapped_column(ForeignKey("tariffs.id"))
    amount: Mapped[int] = mapped_column(BigInteger)
    discount: Mapped[int] = mapped_column(BigInteger)
    method: Mapped[str] = mapped_column(String(20))
    installment_months: Mapped[int | None] = mapped_column(SmallInteger)
    schedule: Mapped[list[int] | None] = mapped_column(JSONB(none_as_null=True))
    email: Mapped[str] = mapped_column(String(320))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
    idempotency_key: Mapped[str | None] = mapped_column(String(200), unique=True)


class ProcessedCommand(Base):
    """Результат и изменение платежа коммитятся в одной транзакции."""

    __tablename__ = "processed_commands"

    request_id: Mapped[UUID] = mapped_column(primary_key=True)
    status_code: Mapped[int] = mapped_column(SmallInteger)
    body: Mapped[dict | list] = mapped_column(JSONB)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), server_default=func.now())
