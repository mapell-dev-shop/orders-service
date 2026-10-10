# orders-service

The orders bounded context of the shop. It takes orders over HTTP, asks inventory-service for the prices, and tracks each order's status from the events other services publish.

## Run it

`./run-all.sh` in the shop workspace starts it with everything else. On its own:

    uv sync
    uv run alembic upgrade head
    uv run uvicorn orders.api:app --port 8001
    uv run python -m orders.worker

## Test it

    uv run pytest
    uv run ruff check . && uv run ruff format --check .
    uv run mypy src

The tests need neither Kafka nor inventory-service. They use an in-memory publisher and a fake catalog.

## Events

* Publishes `OrderPlaced`.
* Publishes `OrderCancelled` when an order is cancelled.
* Consumes `StockRejected` and `PaymentFailed`, and marks a `PENDING` order `REJECTED`.
* Consumes `PaymentCaptured` and marks a `PENDING` order `CONFIRMED`.
* Consumes `PaymentRefunded` and records `refunded_at` on the order, which `GET /orders/{id}` shows. The status does not change.
* `POST /orders/{id}/cancel` moves a `PENDING` or `CONFIRMED` order to `CANCELLED` and publishes `OrderCancelled`, so inventory gives the stock back and payments refunds the money. Cancelling a `CANCELLED` order publishes it again. A `REJECTED` order cannot be cancelled (409). Events never move a `CANCELLED` order.

## Known limitations

* `OrderPlaced` and `OrderCancelled` are published after the database commit. If publishing fails or the process dies in between, a lost `OrderPlaced` leaves a `PENDING` order nobody else hears about, and a lost `OrderCancelled` leaves stock reserved and money captured until the customer cancels again, which publishes it again. A failed publish makes `POST /orders` answer 500. A failed cancel answers 500 with the order already `CANCELLED`. The outbox pattern fixes this. The design of cancelling is in `../docs/superpowers/specs/2026-10-10-cancel-order-compensation-design.md` in the shop workspace.
* Run one worker at a time.
* There is no authentication. Anyone who knows an order id can read or cancel the order.
* Out of scope for cancelling: cancelling part of an order, failing refunds, inventory overselling under concurrent reservations, and a cutoff after which an order can no longer be cancelled (authentication on cancel is covered above).
