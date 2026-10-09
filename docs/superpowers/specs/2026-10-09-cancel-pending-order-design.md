# Cancel a pending order

Date: 2026-10-09. Scope: orders-service only.

## Goal

A customer who placed an order by mistake can cancel it right after clicking buy, while the order is still `PENDING`.

This iteration changes orders-service only. It adds no event and does not touch `contracts`, inventory-service or payments-service. The rest of the shop is not told about a cancellation, so **cancelling is not offered to customers until the next iteration ships** (see Known limitations).

### Success criteria

* A `PENDING` order can be cancelled and becomes `CANCELLED`.
* `CONFIRMED` and `REJECTED` orders cannot be cancelled.
* A cancel racing an incoming event never loses an update: exactly one of them moves the order out of `PENDING`, and the other changes nothing.
* Late or duplicate events never move a `CANCELLED` order.

## API

`POST /orders/{order_id}/cancel`, no request body. It publishes no event.

| Order state | Response |
| --- | --- |
| `PENDING` | `200`, `OrderOut` with `status: "CANCELLED"` and `status_reason: "cancelled by customer"` |
| `CANCELLED` | `200`, the same body. A repeated cancel is harmless. |
| `CONFIRMED` or `REJECTED` | `409`, `"order is <STATUS> and can no longer be cancelled"`. The order is unchanged. |
| unknown id | `404`, `"order not found"` |

The endpoint calls `move_pending` (below) and commits. If the move did not win, it reads the order and picks 404, 200 or 409 from the order's state. The decision comes from the guarded update, never from a read made before it.

## Data model

* `OrderStatus` gains `CANCELLED`.
* `orders.status` is a `String(20)` with no check constraint, so no Alembic revision is needed. `tests/test_migrations.py` confirms that models and migrations still agree.
* No `cancelled_at` column. Nothing reads it, and `status_reason` records why the order ended.

## Guarded status change: `orders/status.py`

One function, used by the API and by every handler:

    move_pending(session, order_id, new_status, reason=None, **fields) -> bool

* It runs `UPDATE orders SET status = :new_status, status_reason = :reason, <fields> WHERE id = :order_id AND status = 'PENDING'` through `session.execute(update(Order)...)` with `synchronize_session=False`.
* It returns `True` exactly when the database reports one matched row. It never decides from objects held in the session. The default `synchronize_session` evaluates the WHERE against in-session objects and applies the SET to them, so a session holding a stale `PENDING` copy would show the new status although the database refused the change.
* After the update it expires the `Order` with that id if the session's identity map holds one, without loading it. The next access then reloads from the database, so the caller sees the real committed state whether or not the move won.
* It does not commit. The caller owns the transaction, so a handler still records its event id in the same commit.

## Handlers

* `reject_order` (used by `handle_stock_rejected` and `handle_payment_failed`) calls `move_pending(session, order_id, OrderStatus.REJECTED, reason=reason)`.
* `handle_payment_captured` calls `move_pending(session, order_id, OrderStatus.CONFIRMED, confirmed_at=now)`.
* Nothing else changes. Each handler returns early if the event id is in `processed_events`, records the id whether or not the move won, and commits once. An event for a `CANCELLED` order is recorded as processed and changes nothing.

## Concurrency

The API and the worker both write `orders.status`. Before this change a handler read the order, saw `PENDING`, and wrote the new status later, so a cancel committed in between would be overwritten.

With `move_pending` no writer reads before it writes. SQLite serializes writers: whichever of the two gets the write lock second evaluates `status = 'PENDING'` against committed data, matches no row, and changes nothing. Under contention the second writer waits on SQLite's busy timeout. Both transactions are one short `UPDATE` plus at most one insert. No retries and no try/except are added.

## Known limitations

These also go into the README's "Known limitations".

* **A cancelled order may still be charged.** Cancelling changes the order in orders-service only. Inventory and payments are not told, so the order flow continues: inventory reserves the stock, and payments usually captures the money. orders-service ignores the resulting `PaymentCaptured`, so the order stays `CANCELLED` while the customer has paid, and nothing refunds them.
* **Reserved stock stays held** for a cancelled order. It is released only if the payment later fails, as today.
* **Therefore cancelling is not offered to customers** until the next iteration ships. The endpoint exists, and no client should expose it.
* There is no authentication. Anyone who knows an order id can cancel it, just as anyone can read it with `GET`.

## Next iteration (outline)

1. **contracts:** add `OrderCancelled` (`{order_id}`), schema `order-cancelled.v1.schema.json`, topic `shop.orders.order-cancelled.v1`. Add `PaymentRefunded` for step 4. Both are new events, so both are additive under VERSIONING.md.
2. **orders-service:** publish `OrderCancelled` after the cancel commits. Until there is an outbox, this has the same publish-after-commit limitation as `OrderPlaced`.
3. **inventory-service:** consume `OrderCancelled` and release the order's reservations, reusing the `PaymentFailed` release logic.
4. **payments-service:** consume `OrderCancelled`. If the order's payment is `CAPTURED`, refund it through the fake gateway (new status `REFUNDED`) and publish `PaymentRefunded`.
5. Offer cancelling to customers only after steps 1 to 4 ship. orders-service may consume `PaymentRefunded` to show the refund.

### Open question: cross-topic ordering

`OrderCancelled` travels on a different topic from the events that start inventory's and payments' work. Keying by `order_id` orders events within one topic, not across topics, so `OrderCancelled` can arrive first:

* **inventory:** `OrderCancelled` arrives before `OrderPlaced`. There is nothing to release yet, and the late `OrderPlaced` reserves stock for a cancelled order.
* **payments:** `OrderCancelled` arrives before `StockReserved`. There is nothing to refund yet, and the late `StockReserved` captures money for a cancelled order.

Likely direction, to be decided in that iteration's spec: each consumer durably records cancelled order ids. A later `OrderPlaced` or `StockReserved` for such an id is skipped, or is rejected with an event so that orders-service still sees a terminal outcome.

## Testing

Test-first, with the existing in-memory publisher and fake catalog. No Kafka.

`tests/test_status.py` (new):

* A `PENDING` order moves, the call returns `True`, and the extra fields (`status_reason`, `confirmed_at`) are set.
* `CONFIRMED`, `REJECTED` and `CANCELLED` orders do not move, and the call returns `False`.
* An unknown order id returns `False`.
* Stale session: session A loads a `PENDING` order, then session B commits another status. A's `move_pending` returns `False`, A's `session.get` shows B's status, and a fresh session shows that the database holds B's status.

`tests/test_api.py`:

* Cancelling a `PENDING` order returns 200 with `CANCELLED` and `"cancelled by customer"`, and `GET` agrees.
* Cancelling twice returns 200 both times with the same body.
* Cancelling a `CONFIRMED` order and cancelling a `REJECTED` order each return 409, and the order is unchanged.
* Cancelling an unknown id returns 404.
* Cancelling publishes nothing. The in-memory publisher holds only the `OrderPlaced` from setup.

`tests/test_handlers.py`:

* The existing tests pass unmodified. They are the regression check for the refactor.
* New: `PaymentCaptured`, `StockRejected` and `PaymentFailed` for a `CANCELLED` order leave it `CANCELLED` with its reason, and record the event id.

Also run, unchanged: `tests/test_migrations.py`, `uv run ruff check .`, `uv run ruff format --check .`, `uv run mypy src`.

The workspace `e2e` suite is out of scope. A cancel test there would mostly show the documented "still charged" limitation, so it belongs to the next iteration.
