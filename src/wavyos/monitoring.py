"""Error reporting to Sentry. Off unless SENTRY_DSN is set; Sentry's free plan is enough for now.

No request bodies, cookies or user details are sent (send_default_pii stays off), and no
performance tracing, which would use up the free quota.
"""
from __future__ import annotations

import os


def init() -> bool:
    dsn = os.environ.get("SENTRY_DSN", "").strip()
    if not dsn:
        return False
    import sentry_sdk
    sentry_sdk.init(dsn=dsn, send_default_pii=False, traces_sample_rate=0.0,
                    environment=os.environ.get("WAVYOS_ENV", "production"))
    return True


def report(exc: BaseException) -> None:
    """Send an error that was caught and recorded (a failed job) so it still reaches Sentry."""
    import sentry_sdk
    sentry_sdk.capture_exception(exc)  # does nothing when init() didn't run
