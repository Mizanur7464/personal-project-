"""
Run the bot from project root:
    python main.py

This adds the project root to sys.path so imports like services.* and core.* work.
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))
if ROOT not in sys.path: 
    sys.path.insert(0, ROOT)

from bot.main import main

if __name__ == "__main__":
    main()
