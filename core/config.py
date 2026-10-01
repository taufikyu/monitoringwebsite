from __future__ import annotations
import os
from pathlib import Path
from dotenv import load_dotenv

BASE_DIR = Path(__file__).resolve().parent.parent
load_dotenv(BASE_DIR / ".env")
load_dotenv()

SECRET_KEY = os.getenv("SECRET_KEY", "mandb-flask-monitor-sqlite-key-2026")
DATABASE_URL = os.getenv("DATABASE_URL")
if not DATABASE_URL:
    sqlite_path = (BASE_DIR / "instance" / "monitor.db").as_posix()
    DATABASE_URL = f"sqlite:///{sqlite_path}"
