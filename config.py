from pathlib import Path
import os

BASE_DIR = Path(__file__).resolve().parent
DATABASE = BASE_DIR / "database" / "labmentor.db"
UPLOAD_DIR = BASE_DIR / "uploads"
# Prefer an operator-provided secret; the local demo gets a non-predictable key.
SECRET_KEY = os.environ.get("LABMENTOR_SECRET") or os.urandom(32)
MAX_CONTENT_LENGTH = 4 * 1024 * 1024
