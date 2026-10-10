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
* `POST /orders/{id}/cancel` moves a `PENDING` or `CONFIRMED` order to `CANCELLED` and publishes `OrderCancelled`, so inventory gives the stock back and payments refunds the money. Cancelling a `CANCELLED` order publishes it again. A `REJECTED` order cannot be cancelled (409). Events never move a `CANCELLED` order. See Known limitations before relying on it.

## Known limitations

* `OrderPlaced` and `OrderCancelled` are published after the database commit. If publishing fails or the process dies in between, the order exists and nobody hears about it. A failed publish makes `POST /orders` answer 500. A failed cancel answers 500 with the order already `CANCELLED`, and cancelling it again publishes `OrderCancelled` again. If `OrderCancelled` is lost, cancelling the order again publishes it again. The outbox pattern fixes this. The design of cancelling is in `../docs/superpowers/specs/2026-10-10-cancel-order-compensation-design.md` in the shop workspace.
* Run one worker at a time.
* There is no authentication. Anyone who knows an order id can read or cancel the order.
