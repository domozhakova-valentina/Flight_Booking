"""Подключение к Booking DB и применение миграций перед запуском сервиса."""

from pathlib import Path

from alembic import command
from alembic.config import Config
from sqlalchemy import URL, create_engine

from . import config


engine = create_engine(URL.create(
    "postgresql+psycopg",
    username=config.DB_USER,
    password=config.DB_PASSWORD,
    host=config.DB_HOST,
    port=5432,
    database=config.DB_NAME,
), pool_pre_ping=True)


def migrate() -> None:
    settings = Config()
    settings.set_main_option("script_location", str(Path(__file__).resolve().parents[1] / "migrations"))
    command.upgrade(settings, "head")
