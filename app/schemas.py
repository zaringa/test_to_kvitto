from datetime import datetime
from enum import StrEnum
from typing import Annotated, Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field, field_validator, model_validator


class PaymentMethod(StrEnum):
    CARD = "card"
    SBP = "sbp"
    INSTALLMENT = "installment"


class PaymentStatus(StrEnum):
    PENDING = "pending"
    SUCCEEDED = "succeeded"
    FAILED = "failed"
    REFUNDED = "refunded"


class PaymentCreate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    tariff_id: Annotated[str, Field(min_length=1, max_length=30)]
    email: EmailStr
    method: PaymentMethod
    installment_months: Annotated[int, Field(strict=True)] | None = None
    promo_code: Annotated[str, Field(max_length=50)] | None = None

    @field_validator("promo_code")
    @classmethod
    def validate_promo_code(cls, value: str | None) -> str | None:
        if value is None:
            return None
        if value.upper() != "KVITTO10":
            raise ValueError("Unknown promo code")
        return value.upper()

    @model_validator(mode="after")
    def validate_installment(self) -> "PaymentCreate":
        if self.method == PaymentMethod.INSTALLMENT:
            if self.installment_months not in (3, 6, 12):
                raise ValueError("Installment requires installment_months: 3, 6 or 12")
        elif self.installment_months is not None:
            raise ValueError("installment_months is only allowed for installment")
        return self


class BankWebhook(BaseModel):
    model_config = ConfigDict(extra="forbid")

    payment_id: UUID
    status: PaymentStatus


class TariffResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: str
    title: str
    price: int


class PaymentResponse(BaseModel):
    model_config = ConfigDict(from_attributes=True)

    id: UUID
    status: PaymentStatus
    tariff_id: str
    amount: int
    discount: int
    method: PaymentMethod
    installment_months: int | None
    schedule: list[int] | None
    email: EmailStr
    created_at: datetime


class Action(StrEnum):
    LIST_TARIFFS = "list_tariffs"
    CREATE_PAYMENT = "create_payment"
    GET_PAYMENT = "get_payment"
    BANK_WEBHOOK = "bank_webhook"


class Command(BaseModel):
    request_id: UUID
    action: Action
    payload: dict[str, Any]


class CommandResult(BaseModel):
    status_code: Annotated[int, Field(ge=200, le=599)]
    body: dict[str, Any] | list[dict[str, Any]]
