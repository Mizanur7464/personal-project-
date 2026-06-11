"""Referral reward tests."""
import os
import tempfile

import core.db as db


def test_referral_grants_bonus_days(monkeypatch, tmp_path):
    monkeypatch.setattr(db, "DB_PATH", str(tmp_path / "test.db"))
    monkeypatch.setattr(db, "REFERRAL_REFEREE_BONUS_DAYS", 3)
    monkeypatch.setattr(db, "REFERRAL_REFERRER_BONUS_DAYS", 3)

    referrer = 1001
    referee = 1002
    db.create_or_update_user(referrer, "ref")
    db.create_or_update_user(referee, "new")
    code = db.ensure_referral_code(referrer)

    ok, msg, referrer_id = db.apply_referral_code(referee, code)
    assert ok is True
    assert referrer_id == referrer
    assert "3 days" in msg
    assert db.get_tier(referee) == "pro"
    assert db.get_tier(referrer) == "pro"
