"""Запуск REST-сервиса с автоматическими миграциями и проверкой доступности БД."""

from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI
from sqlalchemy import text
import uvicorn

from .db import engine, migrate
from .api import router
from .flight_client import channel


@asynccontextmanager
async def lifespan(app: FastAPI):
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    migrate()
    print("Booking DB: migrations applied", flush=True)
    try:
        yield
    finally:
        channel.close()
        engine.dispose()


app = FastAPI(title="Booking Service", lifespan=lifespan)
app.include_router(router)


@app.get("/health")
def health():
    with engine.connect() as connection:
        connection.execute(text("SELECT 1"))
    return {"status": "ok"}


if __name__ == "__main__":
    uvicorn.run(app, host="0.0.0.0", port=8000)
