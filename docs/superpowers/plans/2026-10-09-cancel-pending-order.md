# Cancel a Pending Order Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A `PENDING` order can be cancelled with `POST /orders/{order_id}/cancel`, and no writer (API or worker) can overwrite another's status change.

**Architecture:** A new module `orders/status.py` holds `move_pending`, a guarded `UPDATE ... WHERE status = 'PENDING'` whose database rowcount decides who wins. The existing handlers and the new endpoint all change status only through it. The change stays inside orders-service: no event is published, and no other repository is touched.

**Tech Stack:** Python 3.13+, FastAPI, SQLAlchemy 2.1 (ORM, SQLite), pytest, ruff, mypy strict, uv.

**Spec:** `docs/superpowers/specs/2026-10-09-cancel-pending-order-design.md`. Read it before starting.

## Global Constraints

* Change orders-service only. Do not touch `../contracts`, inventory-service or payments-service, and add no event. The cancel endpoint publishes nothing.
* No Alembic revision. `orders.status` is a `String(20)` without a check constraint, and `tests/test_migrations.py` must still pass.
* `move_pending` runs its update with `synchronize_session=False`, decides only from the database rowcount, expires the session's copy of the order, and never commits.
* No retries and no try/except around status changes.
* Exact strings: `status_reason` `"cancelled by customer"`; 409 detail `"order is <STATUS> and can no longer be cancelled"`; 404 detail `"order not found"`.
* Existing tests in `tests/test_handlers.py` and `tests/test_api.py` stay unmodified. New tests are appended.
* Don't touch any `*.db` file.
* Checks: `uv run pytest`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src` (strict, line length 100).
* Every commit message ends with `Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>`.

## Review Focus

1. A worker session holds a `PENDING` copy of an order, and a cancel commits from another session before the worker handles `PaymentCaptured`. The order must stay `CANCELLED`. Pinned in Task 2: `test_payment_captured_does_not_overwrite_a_cancel_committed_after_the_order_was_read`.
2. The API session holds a `PENDING` copy, and the worker commits `CONFIRMED` before the cancel arrives. The cancel must answer 409, not 200 from the stale copy. Pinned in Task 3: `test_cancelling_an_order_that_is_no_longer_pending_is_refused` (the status is set from a second session).
3. inventory-service is down. Cancelling must still work, because it never needs a price. Pinned in Task 3: `test_cancelling_does_not_need_inventory`.
4. A repeated cancel must return the same body and keep the first `status_reason`, total and lines. Pinned in Task 3: `test_cancelling_twice_returns_the_same_order`.
5. A redelivered event for a `CANCELLED` order is recorded once and changes nothing. Pinned in Task 2: `test_a_late_event_leaves_a_cancelled_order_alone` (records exactly one processed event).

---

### Task 1: `CANCELLED` status and `move_pending`

**Files:**
- Modify: `src/orders/models.py` (the `OrderStatus` enum)
- Create: `src/orders/status.py`
- Test: `tests/test_status.py` (new)

**Interfaces:**
- Consumes: `Order`, `OrderStatus` from `orders.models`; `Base` from `orders.db`; `pending_order(session, order_id="o-1") -> Order` from `tests/test_handlers.py` (existing helper, commits a `PENDING` order with id `o-1`).
- Produces:
  - `OrderStatus.CANCELLED = "CANCELLED"`
  - `orders.status.move_pending(session: Session, order_id: str, new_status: OrderStatus, reason: str | None = None, **fields: Any) -> bool`. It sets `status = new_status` and `status_reason = reason` (so `None` clears it), plus any extra column values in `fields` (e.g. `confirmed_at=...`), only if the row is `PENDING` in the database. Returns `True` when it moved the order. It does not commit, and afterwards the session's copy of that order (if any) is expired.

- [ ] **Step 1: Write the failing tests**

Create `tests/test_status.py`:

```python
from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, create_engine
from sqlalchemy.orm import Session

from orders.db import Base
from orders.models import Order, OrderStatus
from orders.status import move_pending
from tests.test_handlers import pending_order


def test_a_pending_order_moves(session: Session) -> None:
    order = pending_order(session)

    assert move_pending(session, "o-1", OrderStatus.REJECTED, reason="insufficient stock")
    session.commit()

    assert order.status == OrderStatus.REJECTED
    assert order.status_reason == "insufficient stock"


def test_extra_fields_are_set_with_the_move(session: Session) -> None:
    order = pending_order(session)
    confirmed_at = datetime(2026, 10, 9, 12, 0)

    assert move_pending(session, "o-1", OrderStatus.CONFIRMED, confirmed_at=confirmed_at)
    session.commit()

    assert order.status == OrderStatus.CONFIRMED
    assert order.status_reason is None
    assert order.confirmed_at == confirmed_at


@pytest.mark.parametrize(
    "status", [OrderStatus.CONFIRMED, OrderStatus.REJECTED, OrderStatus.CANCELLED]
)
def test_an_order_that_is_not_pending_does_not_move(session: Session, status: OrderStatus) -> None:
    order = pending_order(session)
    order.status = status
    order.status_reason = "earlier"
    session.commit()

    assert not move_pending(session, "o-1", OrderStatus.CANCELLED, reason="later")
    session.commit()

    assert order.status == status
    assert order.status_reason == "earlier"


def test_an_unknown_order_does_not_move(session: Session) -> None:
    assert not move_pending(session, "missing", OrderStatus.CANCELLED)


@pytest.fixture
def file_engine(tmp_path: Path) -> Iterator[Engine]:
    """A database two sessions can share with real, separate transactions."""
    engine = create_engine(f"sqlite:///{tmp_path / 'orders.db'}")
    Base.metadata.create_all(engine)
    yield engine
    engine.dispose()


def test_a_stale_copy_in_the_session_does_not_decide(file_engine: Engine) -> None:
    with Session(file_engine) as worker, Session(file_engine) as api:
        stale = pending_order(worker)
        assert stale.status == OrderStatus.PENDING  # the worker now holds a PENDING copy

        assert move_pending(api, "o-1", OrderStatus.CANCELLED, reason="cancelled by customer")
        api.commit()

        assert not move_pending(
            worker, "o-1", OrderStatus.CONFIRMED, confirmed_at=datetime.now(UTC)
        )
        worker.commit()

        assert stale.status == OrderStatus.CANCELLED
        assert worker.get(Order, "o-1") is stale

    with Session(file_engine) as fresh:
        stored = fresh.get(Order, "o-1")
        assert stored is not None
        assert stored.status == OrderStatus.CANCELLED
        assert stored.status_reason == "cancelled by customer"
        assert stored.confirmed_at is None
```

Notes for the implementer:
* `confirmed_at` is a timezone-naive `DateTime` column, and SQLite returns naive values. That is why `test_extra_fields_are_set_with_the_move` uses a naive datetime.
* The stale-copy test uses a file database on purpose. The shared `session` fixture is one in-memory connection, so it can't show two independent sessions.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_status.py -v`
Expected: collection error, `ModuleNotFoundError: No module named 'orders.status'`.

- [ ] **Step 3: Add the status**

In `src/orders/models.py`, extend the enum:

```python
class OrderStatus(StrEnum):
    PENDING = "PENDING"
    CONFIRMED = "CONFIRMED"
    REJECTED = "REJECTED"
    CANCELLED = "CANCELLED"
```

- [ ] **Step 4: Write `move_pending`**

Create `src/orders/status.py`:

```python
"""The one way an order leaves PENDING. The database decides, not the session."""

from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from orders.models import Order, OrderStatus


def move_pending(
    session: Session,
    order_id: str,
    new_status: OrderStatus,
    reason: str | None = None,
    **fields: Any,
) -> bool:
    """Move the order to new_status if the database still has it PENDING. Does not commit.

    Returns whether this call moved it. Whoever won, the session's copy of the order is
    expired, so the next read shows what the database holds.
    """
    result = cast(
        CursorResult[Any],
        session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status == OrderStatus.PENDING)
            .values(status=new_status, status_reason=reason, **fields),
            # The default would apply the SET to a stale in-session copy that still looks
            # PENDING, even when the database matched no row.
            execution_options={"synchronize_session": False},
        ),
    )
    held = session.identity_map.get(Session.identity_key(Order, order_id))
    if held is not None:
        session.expire(held)
    return result.rowcount == 1
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/test_status.py -v`
Expected: 7 passed (the parametrized test counts three times).

- [ ] **Step 6: Prove the stale-copy test bites**

Temporarily delete the three lines that look up and expire `held` in `src/orders/status.py`, then run:

Run: `uv run pytest tests/test_status.py::test_a_stale_copy_in_the_session_does_not_decide -v`
Expected: FAIL at `assert stale.status == OrderStatus.CANCELLED` (it is still `PENDING`).

Restore the lines exactly as in Step 4 and run the test again. Expected: PASS.

- [ ] **Step 7: Run every check**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all tests pass (including `tests/test_migrations.py`, which proves no migration is needed), ruff reports nothing, and mypy prints `Success`.

- [ ] **Step 8: Commit**

```bash
git add src/orders/models.py src/orders/status.py tests/test_status.py
git commit -m "feat: guarded status change for PENDING orders

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 2: Handlers change status only through `move_pending`

**Files:**
- Modify: `src/orders/handlers.py` (`reject_order`, `handle_payment_captured`, imports)
- Test: `tests/test_handlers.py` (append only; existing tests stay unmodified)

**Interfaces:**
- Consumes: `move_pending(session, order_id, new_status, reason=None, **fields) -> bool` and `OrderStatus.CANCELLED` from Task 1.
- Produces: no new names. `handle_stock_rejected`, `handle_payment_failed` and `handle_payment_captured` keep their signatures `(session: Session, envelope: dict[str, Any]) -> None`.

- [ ] **Step 1: Write the new tests**

Append to the end of `tests/test_handlers.py`. Add `from collections.abc import Callable`, `from typing import Any` and `import pytest` to the file's imports, and `from orders.status import move_pending`, keeping ruff's import order:

```python
def cancelled_order(session: Session, order_id: str = "o-1") -> Order:
    order = pending_order(session, order_id)
    order.status = OrderStatus.CANCELLED
    order.status_reason = "cancelled by customer"
    session.commit()
    return order


def payment_failed(order_id: str = "o-1") -> dict[str, object]:
    return event("PaymentFailed", {"order_id": order_id, "reason": "amount over limit"})


@pytest.mark.parametrize(
    ("handle", "make_event"),
    [
        pytest.param(handle_payment_captured, payment_captured, id="PaymentCaptured"),
        pytest.param(handle_stock_rejected, stock_rejected, id="StockRejected"),
        pytest.param(handle_payment_failed, payment_failed, id="PaymentFailed"),
    ],
)
def test_a_late_event_leaves_a_cancelled_order_alone(
    session: Session,
    handle: Callable[[Session, dict[str, Any]], None],
    make_event: Callable[[], dict[str, Any]],
) -> None:
    order = cancelled_order(session)
    envelope = make_event()

    handle(session, envelope)
    handle(session, envelope)

    assert order.status == OrderStatus.CANCELLED
    assert order.status_reason == "cancelled by customer"
    assert order.confirmed_at is None
    assert session.scalar(select(func.count()).select_from(ProcessedEvent)) == 1


def test_payment_captured_does_not_overwrite_a_cancel_committed_after_the_order_was_read(
    session: Session,
) -> None:
    order = pending_order(session)
    assert order.status == OrderStatus.PENDING  # the worker's session now holds a PENDING copy
    with Session(session.get_bind()) as api_session:
        assert move_pending(
            api_session, "o-1", OrderStatus.CANCELLED, reason="cancelled by customer"
        )
        api_session.commit()

    handle_payment_captured(session, payment_captured())

    assert order.status == OrderStatus.CANCELLED
    assert order.confirmed_at is None
    with Session(session.get_bind()) as fresh:
        stored = fresh.get(Order, "o-1")
        assert stored is not None
        assert stored.status == OrderStatus.CANCELLED
```

Note for the implementer: `payment_captured` is defined in the middle of the existing file, so these tests must go after it, at the end of the file.

- [ ] **Step 2: Run the new tests against the old handlers**

Run: `uv run pytest tests/test_handlers.py -v`
Expected: `test_payment_captured_does_not_overwrite_a_cancel_committed_after_the_order_was_read` FAILS with the order `CONFIRMED`. That's the lost update this change fixes. The three `test_a_late_event_leaves_a_cancelled_order_alone` cases already PASS, because the old handlers also check `PENDING`, and they keep that behaviour pinned through the refactor. All existing tests PASS.

- [ ] **Step 3: Use `move_pending` in the handlers**

Replace `src/orders/handlers.py` with:

```python
"""What orders-service does when an event arrives from another service."""

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from orders.models import OrderStatus, ProcessedEvent
from orders.status import move_pending


def reject_order(session: Session, envelope: dict[str, Any], reason: str) -> None:
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    move_pending(session, envelope["data"]["order_id"], OrderStatus.REJECTED, reason=reason)
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()


def handle_stock_rejected(session: Session, envelope: dict[str, Any]) -> None:
    data = envelope["data"]
    reject_order(session, envelope, f"{data['reason']}: {data['sku']}")


def handle_payment_failed(session: Session, envelope: dict[str, Any]) -> None:
    reject_order(session, envelope, f"payment failed: {envelope['data']['reason']}")


def handle_payment_captured(session: Session, envelope: dict[str, Any]) -> None:
    if session.get(ProcessedEvent, envelope["event_id"]) is not None:
        return
    move_pending(
        session,
        envelope["data"]["order_id"],
        OrderStatus.CONFIRMED,
        confirmed_at=datetime.now(UTC),
    )
    session.add(ProcessedEvent(event_id=envelope["event_id"]))
    session.commit()
```

The `Order` import is gone because nothing in the module reads orders any more. The event id is still recorded whether or not the move won, in the same commit.

- [ ] **Step 4: Run the handler tests**

Run: `uv run pytest tests/test_handlers.py -v`
Expected: all pass, including every pre-existing test, unmodified.

- [ ] **Step 5: Run every check**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all pass, ruff reports nothing, and mypy prints `Success`.

- [ ] **Step 6: Commit**

```bash
git add src/orders/handlers.py tests/test_handlers.py
git commit -m "fix: handlers can no longer overwrite a status committed by another session

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```

---

### Task 3: `POST /orders/{order_id}/cancel` and the documented limitation

**Files:**
- Modify: `src/orders/api.py` (new endpoint after `get_order`, one import)
- Modify: `README.md` ("Known limitations")
- Test: `tests/test_api.py` (append only)

**Interfaces:**
- Consumes: `move_pending` and `OrderStatus.CANCELLED` from Task 1; the existing `SessionDep`, `to_out`, `OrderOut` in `api.py`; the `client`, `session`, `publisher`, `catalog` fixtures and `TWO_BOOKS_ONE_PEN` in the tests.
- Produces: `cancel_order(order_id: str, session: SessionDep) -> OrderOut`, routed as `POST /orders/{order_id}/cancel`, answering 200 / 404 / 409 as the spec's table says.

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_api.py`. Add `import pytest` and `from sqlalchemy import update` (merge into the existing `from sqlalchemy import ...` line), and change `from orders.models import Order` to `from orders.models import Order, OrderStatus`:

```python
def place(client: TestClient) -> dict[str, object]:
    response = client.post("/orders", json=TWO_BOOKS_ONE_PEN)
    assert response.status_code == 201
    body: dict[str, object] = response.json()
    return body


def set_status_elsewhere(
    session: Session, order_id: str, status: str, reason: str | None
) -> None:
    """Commit a status from a session of its own, as the worker would.

    The client's session keeps its PENDING copy of the order, so this also checks that
    the endpoint does not decide from a stale copy.
    """
    with Session(session.get_bind()) as worker:
        worker.execute(
            update(Order)
            .where(Order.id == order_id)
            .values(status=status, status_reason=reason)
        )
        worker.commit()


def test_cancelling_a_pending_order(client: TestClient) -> None:
    placed = place(client)

    response = client.post(f"/orders/{placed['id']}/cancel")

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "CANCELLED"
    assert body["status_reason"] == "cancelled by customer"
    assert body["total_cents"] == placed["total_cents"]
    assert body["lines"] == placed["lines"]
    assert client.get(f"/orders/{placed['id']}").json() == body


def test_cancelling_twice_returns_the_same_order(client: TestClient) -> None:
    order_id = place(client)["id"]

    first = client.post(f"/orders/{order_id}/cancel")
    second = client.post(f"/orders/{order_id}/cancel")

    assert first.status_code == second.status_code == 200
    assert second.json() == first.json()


@pytest.mark.parametrize(
    ("status", "reason"),
    [(OrderStatus.CONFIRMED, None), (OrderStatus.REJECTED, "insufficient stock: BOOK-1")],
)
def test_cancelling_an_order_that_is_no_longer_pending_is_refused(
    client: TestClient, session: Session, status: OrderStatus, reason: str | None
) -> None:
    order_id = str(place(client)["id"])
    set_status_elsewhere(session, order_id, status, reason)

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 409
    assert response.json()["detail"] == f"order is {status} and can no longer be cancelled"
    after = client.get(f"/orders/{order_id}").json()
    assert after["status"] == status
    assert after["status_reason"] == reason


def test_cancelling_a_missing_order_gives_404(client: TestClient) -> None:
    response = client.post("/orders/does-not-exist/cancel")

    assert response.status_code == 404
    assert response.json()["detail"] == "order not found"


def test_cancelling_publishes_nothing(
    client: TestClient, publisher: InMemoryEventPublisher
) -> None:
    order_id = place(client)["id"]

    client.post(f"/orders/{order_id}/cancel")

    assert [envelope["type"] for envelope in publisher.published] == ["OrderPlaced"]


def test_cancelling_does_not_need_inventory(client: TestClient, catalog: FakeCatalog) -> None:
    order_id = place(client)["id"]
    catalog.error = httpx2.ConnectError("connection refused")
    catalog.requested.clear()

    response = client.post(f"/orders/{order_id}/cancel")

    assert response.status_code == 200
    assert catalog.requested == []
```

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/test_api.py -v`
Expected: every new test FAILS. With no route yet, each cancel gets FastAPI's `404 {"detail": "Not Found"}`, so even the 404 test fails on its detail. All existing tests PASS.

- [ ] **Step 3: Write the endpoint**

In `src/orders/api.py`, add the import next to the other `orders` imports:

```python
from orders.status import move_pending
```

Then add after `get_order`:

```python
@app.post("/orders/{order_id}/cancel")
def cancel_order(order_id: str, session: SessionDep) -> OrderOut:
    """Cancel a PENDING order. Only orders-service hears of it (README, Known limitations)."""
    moved = move_pending(session, order_id, OrderStatus.CANCELLED, reason="cancelled by customer")
    session.commit()
    order = session.get(Order, order_id)
    if order is None:
        raise HTTPException(404, "order not found")
    if not moved and order.status != OrderStatus.CANCELLED:
        raise HTTPException(409, f"order is {order.status} and can no longer be cancelled")
    return to_out(order)
```

Why it is shaped this way:
* `move_pending` decides; the read afterwards only picks the answer. Because `move_pending` expired the session's copy, `session.get` returns what the database holds, not the `PENDING` copy left over from placing the order.
* When the move did not win, the commit is a no-op. Committing unconditionally keeps the function linear.
* An order that was already `CANCELLED` falls through to 200 with its stored body. That makes a repeated cancel harmless.

- [ ] **Step 4: Run the API tests**

Run: `uv run pytest tests/test_api.py -v`
Expected: all pass.

- [ ] **Step 5: Document the limitation**

In `README.md`, append these bullets to the end of "## Known limitations":

```markdown
* `POST /orders/{id}/cancel` cancels a `PENDING` order in orders-service only. Inventory and payments are not told, so the order flow carries on: the stock stays reserved (unless the payment later fails), and the payment is usually captured. The order stays `CANCELLED` and the customer is charged, with no refund. Cancelling is therefore not offered to customers until inventory and payments learn about it. The design for that is in `docs/superpowers/specs/2026-10-09-cancel-pending-order-design.md`.
* There is no authentication. Anyone who knows an order id can read or cancel the order.
```

- [ ] **Step 6: Run every check**

Run: `uv run pytest && uv run ruff check . && uv run ruff format --check . && uv run mypy src`
Expected: all tests pass (`test_status.py`, `test_handlers.py`, `test_api.py`, `test_migrations.py`, `test_catalog.py`), ruff reports nothing, and mypy prints `Success`. If `ruff format --check` complains, run `uv run ruff format .` and re-run the checks.

- [ ] **Step 7: Commit**

```bash
git add src/orders/api.py tests/test_api.py README.md
git commit -m "feat: let a customer cancel a pending order

Only orders-service hears of the cancel; see README, Known limitations.

Co-Authored-By: Claude Opus 5.5 <noreply@anthropic.com>"
```
