# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## Commands

    uv run pytest                                   # needs neither Kafka nor inventory-service
    uv run pytest tests/test_handlers.py::test_payment_failed_rejects_the_order
    uv run ruff check . && uv run ruff format --check .
    uv run mypy src                                 # strict

The full flow runs from the workspace root: `./up.sh`, `./run-all.sh`, then `uv run pytest e2e`.

## Architecture rules

* Never read inventory-service's or payments-service's database. Prices come from `GET /products/{sku}` on inventory-service, and everything else comes from events.
* Every handler is idempotent. It returns early if `envelope["event_id"]` is already in `processed_events`, and adds the id in the same commit as the state change.
* Events only move a `PENDING` order. A late or duplicate event must never change the status of a `CONFIRMED`, `REJECTED` or `CANCELLED` order. Every status change goes through `move_order()`.
* `OrderPlaced` and `OrderCancelled` are published after the commit, and there is no outbox yet. Don't paper over this with retries or try/except. It is a documented limitation (README).
* Run only one worker at a time.

## Topics and event schemas

Never hard-code topic names or event payload shapes. `shop_contracts` (the sibling `../contracts` repo, installed editable) maps event types to topics and schemas, and validates every envelope. Change schemas there first, never here:

@../contracts/VERSIONING.md

## Migrations

Every model change needs a new Alembic revision. `tests/test_migrations.py` fails if `alembic upgrade head` differs from the models. `.claude/rules/migrations.md` holds the rules for writing revisions.

## Don't touch

* `../contracts` from this repo's tasks. A schema change is its own change, made there first.
* The services' `*.db` files. `../checkout-lesson.sh` deletes them, and `alembic upgrade head` or `../run-all.sh` recreates them.
