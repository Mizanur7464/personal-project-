"""SQLite database backup utility."""
from __future__ import annotations

import shutil
from datetime import datetime
from pathlib import Path

from core.db import DB_PATH


def backup_database() -> Path:
    """Copy bot.db to data/backups/bot-YYYYMMDD-HHMMSS.db. Returns backup path."""
    src = Path(DB_PATH)
    if not src.exists():
        raise FileNotFoundError(f"Database not found: {src}")
    backup_dir = src.parent / "backups"
    backup_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.utcnow().strftime("%Y%m%d-%H%M%S")
    dest = backup_dir / f"bot-{stamp}.db"
    shutil.copy2(src, dest)
    return dest
