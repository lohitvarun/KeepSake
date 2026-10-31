import os
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent

# On Render, set DATA_DIR=/var/data and attach a persistent disk there.
# Locally, the app keeps using its project folders.
DATA_DIR = Path(os.environ.get("DATA_DIR", str(BASE_DIR)))
DATABASE_DIR = DATA_DIR / ("database" if DATA_DIR == BASE_DIR else "")
DATABASE = DATABASE_DIR / "diary.db"
UPLOAD_FOLDER = DATA_DIR / ("uploads" if DATA_DIR == BASE_DIR else "uploads")

SECRET_KEY = os.environ.get("DIARY_SECRET_KEY", "change-this-development-key-before-deployment")
MAX_CONTENT_LENGTH = 25 * 1024 * 1024

DATABASE.parent.mkdir(parents=True, exist_ok=True)
UPLOAD_FOLDER.mkdir(parents=True, exist_ok=True)
