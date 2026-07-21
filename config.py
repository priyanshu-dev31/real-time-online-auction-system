import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent

# Load local environment variables
load_dotenv(BASE_DIR / ".env")


class Config:

    SECRET_KEY = os.environ.get("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL"
    )

    SQLALCHEMY_TRACK_MODIFICATIONS = False

    UPLOAD_FOLDER = str(
        BASE_DIR / "static" / "uploads"
    )

    MAX_CONTENT_LENGTH = 5 * 1024 * 1024


# Stop the application if required secrets are missing
if not Config.SECRET_KEY:
    raise RuntimeError(
        "SECRET_KEY is missing from the .env file."
    )

if not Config.SQLALCHEMY_DATABASE_URI:
    raise RuntimeError(
        "DATABASE_URL is missing from the .env file."
    )