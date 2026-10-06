import hashlib
import hmac
from contextlib import asynccontextmanager
from typing import Annotated
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from redis.exceptions import RedisError

from app.broker import RedisBroker, make_redis
from app.config import Settings
from app.schemas import Action, BankWebhook, PaymentCreate, PaymentResponse, TariffResponse


def create_app(settings: Settings | None = None) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI):
        redis = make_redis(settings)
        app.state.broker = RedisBroker(redis, settings)
        try:
            yield
        finally:
            await redis.aclose()

    app = FastAPI(title="Квитто — API платежей", version="0.1.0", lifespan=lifespan)

    async def send(request: Request, action: Action, payload: dict) -> JSONResponse:
        try:
            result = await request.app.state.broker.request(action, payload)
        except TimeoutError as error:
            raise HTTPException(504, "worker_timeout") from error
        except RedisError as error:
            raise HTTPException(503, "broker_unavailable") from error
        return JSONResponse(status_code=result.status_code, content=result.body)

    @app.get("/health", tags=["Service"])
    async def health(request: Request):
        broker = request.app.state.broker
        try:
            ready = await broker.redis.exists(broker.heartbeat_key)
        except RedisError as error:
            raise HTTPException(503, "broker_unavailable") from error
        if not ready:
            raise HTTPException(503, "worker_unavailable")
        return {"status": "ok"}

    @app.get("/tariffs", response_model=list[TariffResponse], tags=["Tariffs"])
    async def list_tariffs(request: Request):
        return await send(request, Action.LIST_TARIFFS, {})

    @app.post(
        "/payments",
        response_model=PaymentResponse,
        status_code=201,
        responses={200: {"model": PaymentResponse}},
        tags=["Payments"],
    )
    async def create_payment(
        request: Request,
        payment: PaymentCreate,
        idempotency_key: Annotated[
            str | None, Header(min_length=1, max_length=200, pattern=r"\S")
        ] = None,
    ):
        return await send(
            request,
            Action.CREATE_PAYMENT,
            {"payment": payment.model_dump(mode="json"), "idempotency_key": idempotency_key},
        )

    @app.get("/payments/{payment_id}", response_model=PaymentResponse, tags=["Payments"])
    async def get_payment(payment_id: UUID, request: Request):
        return await send(request, Action.GET_PAYMENT, {"payment_id": str(payment_id)})

    @app.post("/webhooks/bank", tags=["Webhooks"])
    async def bank_webhook(
        request: Request,
        webhook: BankWebhook,
        x_signature: Annotated[str | None, Header()] = None,
    ):
        if settings.webhook_secret:
            signature = hmac.new(
                settings.webhook_secret.encode(), await request.body(), hashlib.sha256
            ).hexdigest()
            if x_signature is None or not hmac.compare_digest(
                signature.encode(), x_signature.encode()
            ):
                raise HTTPException(401, "invalid_signature")
        return await send(request, Action.BANK_WEBHOOK, webhook.model_dump(mode="json"))

    return app


app = create_app()
