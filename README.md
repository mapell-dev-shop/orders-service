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
* Consumes `StockRejected` and `PaymentFailed`, and marks the order `REJECTED`.

## Known limitations

* The event is published after the database commit. If the process dies in between, the order exists and nobody hears about it. The outbox pattern fixes this.
* Run one worker at a time.
