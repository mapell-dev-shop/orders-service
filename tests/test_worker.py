from typing import Any

import pytest

from orders import worker


def test_the_worker_listens_for_every_event_it_handles(monkeypatch: pytest.MonkeyPatch) -> None:
    subscribed: dict[str, Any] = {}
    monkeypatch.setattr(
        worker, "run_consumer", lambda servers, group, handlers: subscribed.update(handlers)
    )

    worker.main()

    assert set(subscribed) == {
        "StockRejected",
        "PaymentFailed",
        "PaymentCaptured",
        "PaymentRefunded",
    }
