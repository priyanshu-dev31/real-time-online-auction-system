import os
from pathlib import Path

from dotenv import load_dotenv


BASE_DIR = Path(__file__).resolve().parent
load_dotenv(BASE_DIR / ".env")


database_url = os.getenv("DATABASE_URL")

# Make the Aiven MySQL URL use PyMySQL
if database_url and database_url.startswith("mysql://"):
    database_url = database_url.replace(
        "mysql://",
        "mysql+pymysql://",
        1,
    )


class Config:
    SECRET_KEY = os.getenv("SECRET_KEY")

    SQLALCHEMY_DATABASE_URI = database_url
    SQLALCHEMY_TRACK_MODIFICATIONS = False

    SQLALCHEMY_ENGINE_OPTIONS = {
        "connect_args": {
            "ssl": {
                "ca": os.getenv(
                    "DB_SSL_CA",
                    str(BASE_DIR / "certs" / "ca.pem"),
                )
            }
        },
        "pool_pre_ping": True,
        "pool_recycle": 280,
    }

    UPLOAD_FOLDER = str(BASE_DIR / "static" / "uploads")
    MAX_CONTENT_LENGTH = 5 * 1024 * 1024


if not Config.SECRET_KEY:
    raise RuntimeError("SECRET_KEY is missing.")

if not Config.SQLALCHEMY_DATABASE_URI:
    raise RuntimeError("DATABASE_URL is missing.")