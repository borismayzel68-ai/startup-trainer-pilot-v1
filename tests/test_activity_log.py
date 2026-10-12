import pytest

from activity_log import ActivityLog


def test_record_and_read():
    log = ActivityLog()
    log.record("startup", "synthetic-1", "login", ts="2026-11-05T10:00:00Z")
    assert log.events("synthetic-1")[0]["event"] == "login"


def test_rejects_unknown_role_and_payment_or_text_meta():
    log = ActivityLog()
    with pytest.raises(ValueError):
        log.record("admin", "u", "x")
    with pytest.raises(ValueError):
        log.record("fund", "u", "x", meta={"payment_status": "paid"})


def test_activation_deadline():
    log = ActivityLog()
    log.record("startup", "s1", "startup_profile_and_readiness_report", ts="2026-11-07T10:00:00Z")
    log.record("startup", "s2", "startup_profile_and_readiness_report", ts="2026-11-12T10:00:00Z")
    assert log.activated("s1", "2026-11-05T10:00:00Z")
    assert not log.activated("s2", "2026-11-05T10:00:00Z")
