import logging
import os
import signal
import threading
from datetime import datetime, timedelta, timezone

from completion_event import post_completion_json
from session_store import DurableSessionStore, iso_z


DEFAULT_POLL_SECONDS = 2.0
DEFAULT_CLAIM_TIMEOUT_SECONDS = 60
DEFAULT_MAX_ATTEMPTS = 8
DEFAULT_RETRY_BASE_SECONDS = 5
DEFAULT_RETRY_MAX_SECONDS = 300
LOGGER = logging.getLogger(__name__)


def utc_now():
    return datetime.now(timezone.utc)


def retry_delay_seconds(attempts, base_seconds, max_seconds):
    exponent = max(0, int(attempts) - 1)
    return min(int(max_seconds), int(base_seconds) * (2 ** exponent))


def deliver_one(
    store,
    webhook_url,
    *,
    post_func=post_completion_json,
    now_value=None,
    timeout_seconds=10,
    claim_timeout_seconds=DEFAULT_CLAIM_TIMEOUT_SECONDS,
    max_attempts=DEFAULT_MAX_ATTEMPTS,
    retry_base_seconds=DEFAULT_RETRY_BASE_SECONDS,
    retry_max_seconds=DEFAULT_RETRY_MAX_SECONDS,
):
    """Deliver one outbox item; return False when no item is ready."""
    if not webhook_url:
        raise ValueError("TRAINER_EVENT_WEBHOOK is required")
    current = now_value or utc_now()
    stale_before = iso_z(
        current - timedelta(seconds=int(claim_timeout_seconds))
    )
    exhausted = store.fail_exhausted_completion_deliveries(
        max_attempts=int(max_attempts),
        stale_before=stale_before,
        failed_at=iso_z(current),
    )
    if exhausted:
        LOGGER.error(
            "%s completion deliveries exhausted after worker interruption",
            exhausted,
        )
    claimed = store.claim_next_completion_delivery(
        claimed_at=iso_z(current),
        stale_before=stale_before,
        max_attempts=int(max_attempts),
    )
    if not claimed:
        return False

    try:
        http_status = post_func(
            webhook_url,
            claimed["payload"],
            timeout_seconds=timeout_seconds,
        )
    except Exception as exc:
        attempts = int(claimed["attempts"])
        terminal = attempts >= int(max_attempts)
        delay = retry_delay_seconds(
            attempts, retry_base_seconds, retry_max_seconds
        )
        store.mark_completion_delivery_failed(
            claimed["idempotency_key"],
            claimed["claim_token"],
            f"{type(exc).__name__}: {exc}",
            available_at=iso_z(current + timedelta(seconds=delay)),
            failed_at=iso_z(current),
            terminal=terminal,
        )
        if terminal:
            LOGGER.error(
                "completion delivery became dead: %s",
                claimed["idempotency_key"],
            )
    else:
        store.mark_completion_delivery_succeeded(
            claimed["idempotency_key"],
            claimed["claim_token"],
            int(http_status),
            delivered_at=iso_z(current),
        )
    return True


def run_forever(store, webhook_url, stop_event, **options):
    poll_seconds = float(options.pop("poll_seconds", DEFAULT_POLL_SECONDS))
    while not stop_event.is_set():
        try:
            processed = deliver_one(store, webhook_url, **options)
        except Exception:
            LOGGER.exception("completion delivery loop failed; retrying")
            processed = False
        if not processed:
            stop_event.wait(poll_seconds)


def main():
    logging.basicConfig(level=os.environ.get("LOG_LEVEL", "INFO"))
    database_url = (
        os.environ.get("SESSION_DATABASE_URL")
        or os.environ.get("DATABASE_URL")
        or "sqlite:///trainer_sessions.db"
    )
    webhook_url = os.environ.get("TRAINER_EVENT_WEBHOOK", "").strip()
    if not webhook_url:
        raise SystemExit("TRAINER_EVENT_WEBHOOK is required")
    store = DurableSessionStore(
        database_url,
        retention_days=int(os.environ.get("SESSION_RETENTION_DAYS", "30")),
    )
    stop_event = threading.Event()

    def stop(_signum, _frame):
        stop_event.set()

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)
    run_forever(
        store,
        webhook_url,
        stop_event,
        poll_seconds=float(
            os.environ.get("DELIVERY_POLL_SECONDS", DEFAULT_POLL_SECONDS)
        ),
        timeout_seconds=float(
            os.environ.get("WEBHOOK_TIMEOUT_SECONDS", "10")
        ),
        claim_timeout_seconds=int(os.environ.get(
            "DELIVERY_CLAIM_TIMEOUT_SECONDS",
            str(DEFAULT_CLAIM_TIMEOUT_SECONDS),
        )),
        max_attempts=int(os.environ.get(
            "DELIVERY_MAX_ATTEMPTS", str(DEFAULT_MAX_ATTEMPTS)
        )),
        retry_base_seconds=int(os.environ.get(
            "DELIVERY_RETRY_BASE_SECONDS",
            str(DEFAULT_RETRY_BASE_SECONDS),
        )),
        retry_max_seconds=int(os.environ.get(
            "DELIVERY_RETRY_MAX_SECONDS",
            str(DEFAULT_RETRY_MAX_SECONDS),
        )),
    )


if __name__ == "__main__":
    main()
