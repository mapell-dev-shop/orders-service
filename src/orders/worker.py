"""Kafka consumer process of orders-service: python -m orders.worker"""

from collections.abc import Callable
from typing import Any

from shop_contracts.messaging import Handler, run_consumer
from sqlalchemy.orm import Session

from orders import settings
from orders.db import SessionLocal
from orders.handlers import handle_payment_failed, handle_stock_rejected


def with_session(handler: Callable[[Session, dict[str, Any]], None]) -> Handler:
    def run(envelope: dict[str, Any]) -> None:
        with SessionLocal() as session:
            handler(session, envelope)

    return run


def main() -> None:
    run_consumer(
        settings.KAFKA_BOOTSTRAP_SERVERS,
        "orders-service",
        {
            "StockRejected": with_session(handle_stock_rejected),
            "PaymentFailed": with_session(handle_payment_failed),
        },
    )


if __name__ == "__main__":
    main()
