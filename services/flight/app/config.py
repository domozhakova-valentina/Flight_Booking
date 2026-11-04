"""Настройки Flight Service из переменных окружения контейнера."""

import os


DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
REDIS_SENTINELS = [
    (host.strip(), 26379)
    for host in os.environ.get("REDIS_SENTINELS", "sentinel-1,sentinel-2,sentinel-3").split(",")
]
REDIS_MASTER_NAME = os.environ.get("REDIS_MASTER_NAME", "flight-cache")
GRPC_API_KEY = os.environ["GRPC_API_KEY"]
if not GRPC_API_KEY or not GRPC_API_KEY.isascii() or not GRPC_API_KEY.isprintable():
    raise ValueError("GRPC_API_KEY must contain printable ASCII characters")
