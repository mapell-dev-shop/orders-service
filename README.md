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
* Consumes `StockRejected` and `PaymentFailed`, and marks a `PENDING` order `REJECTED`.
* Consumes `PaymentCaptured` and marks a `PENDING` order `CONFIRMED`.
* `POST /orders/{id}/cancel` moves a `PENDING` order to `CANCELLED`. Events never move a `CANCELLED` order. See Known limitations before relying on it.

## Known limitations

* The event is published after the database commit. If publishing fails or the process dies in between, the order exists and nobody hears about it. A failed publish makes `POST /orders` answer 500. The outbox pattern fixes this.
* Run one worker at a time.
* `POST /orders/{id}/cancel` cancels a `PENDING` order in orders-service only. Inventory and payments are not told, so the order flow carries on: the stock stays reserved (unless the payment later fails), and the payment is usually captured. The order stays `CANCELLED` and the customer is charged, with no refund. Cancelling is therefore not offered to customers until inventory and payments learn about it. The design for that is in `docs/superpowers/specs/2026-10-09-cancel-pending-order-design.md`.
* There is no authentication. Anyone who knows an order id can read or cancel the order.
