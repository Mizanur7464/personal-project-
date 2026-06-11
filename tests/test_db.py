"""Unit tests for database tier expiry."""
from datetime import datetime, timedelta

import core.db as db


def test_tier_expiry_downgrade(monkeypatch, tmp_path):
    db_path = tmp_path / "test.db"
    monkeypatch.setattr(db, "DB_PATH", str(db_path))

    uid = 999001
    db.create_or_update_user(uid, "tester")
    expired = (datetime.utcnow() - timedelta(days=1)).isoformat() + "Z"
    db.set_tier(uid, "pro", expires_at=expired)

    assert db.get_tier(uid) == "free"
