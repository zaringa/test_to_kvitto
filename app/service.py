from uuid import UUID

from sqlalchemy import select
from sqlalchemy.dialects.postgresql import insert
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from app.domain import build_schedule, calculate_amount, can_transition
from app.models import Payment, ProcessedCommand, Tariff
from app.schemas import (
    Action,
    BankWebhook,
    Command,
    CommandResult,
    PaymentCreate,
    PaymentMethod,
    PaymentResponse,
    PaymentStatus,
    TariffResponse,
)


def payment_result(payment: Payment, status_code: int = 200) -> CommandResult:
    return CommandResult(
        status_code=status_code,
        body=PaymentResponse.model_validate(payment).model_dump(mode="json"),
    )


def not_found(detail: str = "payment_not_found") -> CommandResult:
    return CommandResult(status_code=404, body={"detail": detail})


async def create_payment(session: AsyncSession, payload: dict) -> CommandResult:
    data = PaymentCreate.model_validate(payload["payment"])
    key = payload.get("idempotency_key")
    if key is not None:
        existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
        if existing is not None:
            return payment_result(existing)

    tariff = await session.get(Tariff, data.tariff_id)
    if tariff is None:
        return not_found("tariff_not_found")

    amount, discount = calculate_amount(tariff.price, data.promo_code)
    schedule = (
        build_schedule(amount, data.installment_months)
        if data.method == PaymentMethod.INSTALLMENT and data.installment_months is not None
        else None
    )
    statement = (
        insert(Payment)
        .values(
            tariff_id=tariff.id,
            amount=amount,
            discount=discount,
            method=data.method.value,
            installment_months=data.installment_months,
            schedule=schedule,
            email=str(data.email),
            idempotency_key=key,
        )
        .on_conflict_do_nothing(index_elements=[Payment.idempotency_key])
        .returning(Payment)
    )
    payment = await session.scalar(statement)
    if payment is not None:
        return payment_result(payment, 201)

    # UNIQUE защищает от дубля, даже если два worker одновременно не нашли ключ.
    existing = await session.scalar(select(Payment).where(Payment.idempotency_key == key))
    if existing is None:
        raise RuntimeError("Conflicting payment was not found")
    return payment_result(existing)


async def apply_webhook(session: AsyncSession, payload: dict) -> CommandResult:
    data = BankWebhook.model_validate(payload)
    # Другой worker дождётся коммита и проверит переход уже из нового статуса.
    
    payment = await session.scalar(
        select(Payment).where(Payment.id == data.payment_id).with_for_update()
    )
    if payment is None:
        return not_found()
    if not can_transition(PaymentStatus(payment.status), data.status):
        return CommandResult(status_code=409, body={"error": "invalid_transition"})
    payment.status = data.status.value
    return CommandResult(status_code=200, body={"result": "ok"})


async def dispatch(session: AsyncSession, command: Command) -> CommandResult:
    match command.action:
        case Action.LIST_TARIFFS:
            tariffs = await session.scalars(select(Tariff).order_by(Tariff.price))
            return CommandResult(
                status_code=200,
                body=[TariffResponse.model_validate(tariff).model_dump() for tariff in tariffs],
            )
        case Action.CREATE_PAYMENT:
            return await create_payment(session, command.payload)
        case Action.GET_PAYMENT:
            payment = await session.get(Payment, UUID(command.payload["payment_id"]))
            return payment_result(payment) if payment is not None else not_found()
        case Action.BANK_WEBHOOK:
            return await apply_webhook(session, command.payload)
    raise ValueError(f"Unsupported action: {command.action}")


async def execute_command(
    sessions: async_sessionmaker[AsyncSession], command: Command
) -> CommandResult:
    async with sessions.begin() as session:
        # INSERT ждёт конкурирующую транзакцию с тем же request_id.
        inserted_id = await session.scalar(
            insert(ProcessedCommand)
            .values(request_id=command.request_id, status_code=500, body={})
            .on_conflict_do_nothing()
            .returning(ProcessedCommand.request_id)
        )
        record = await session.get(ProcessedCommand, command.request_id)
        if record is None:
            raise RuntimeError("Command record was not found")
        if inserted_id is None:
            return CommandResult(status_code=record.status_code, body=record.body)

        result = await dispatch(session, command)
        record.status_code = result.status_code
        record.body = result.body
        return result
