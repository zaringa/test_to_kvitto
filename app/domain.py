from app.schemas import PaymentStatus

ALLOWED_TRANSITIONS = {
    PaymentStatus.PENDING: {PaymentStatus.SUCCEEDED, PaymentStatus.FAILED},
    PaymentStatus.SUCCEEDED: {PaymentStatus.REFUNDED},
    PaymentStatus.FAILED: set(),
    PaymentStatus.REFUNDED: set(),
}


def calculate_amount(price: int, promo_code: str | None) -> tuple[int, int]:
    """Возвращает (сумма к оплате, скидка) в целых копейках."""
    discount = price // 10 if promo_code == "KVITTO10" else 0
    return price - discount, discount


def build_schedule(amount: int, months: int) -> list[int]:
    """Остаток от деления распределяется по одной копейке в первые платежи."""
    if months not in (3, 6, 12):
        raise ValueError("Supported installment terms: 3, 6, 12")
    if amount <= 0:
        raise ValueError("Amount must be positive")
    regular_payment, remainder = divmod(amount, months)
    return [regular_payment + (1 if month < remainder else 0) for month in range(months)]


def can_transition(current: PaymentStatus, target: PaymentStatus) -> bool:
    return target in ALLOWED_TRANSITIONS[current]
