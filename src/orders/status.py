"""The one way an order changes status. The database decides, not the session."""

from typing import Any, cast

from sqlalchemy import CursorResult, update
from sqlalchemy.orm import Session

from orders.models import Order, OrderStatus


def move_order(
    session: Session,
    order_id: str,
    new_status: OrderStatus,
    *,
    from_statuses: tuple[OrderStatus, ...] = (OrderStatus.PENDING,),
    reason: str | None = None,
    **fields: Any,
) -> bool:
    """Move the order to new_status if the database still has it in one of from_statuses.

    Does not commit. Returns whether this call moved it. Whoever won, the session's copy of
    the order is expired, so the next read shows what the database holds.
    """
    result = cast(
        CursorResult[Any],
        session.execute(
            update(Order)
            .where(Order.id == order_id, Order.status.in_(from_statuses))
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
