"""Настройки Booking Service из переменных окружения контейнера."""

import os


DB_HOST = os.environ["DB_HOST"]
DB_NAME = os.environ["DB_NAME"]
DB_USER = os.environ["DB_USER"]
DB_PASSWORD = os.environ["DB_PASSWORD"]
FLIGHT_SERVICE_ADDRESS = os.environ.get("FLIGHT_SERVICE_ADDRESS", "flight:50051")
CB_FAILURE_THRESHOLD = int(os.environ.get("CB_FAILURE_THRESHOLD", "5"))
CB_RESET_TIMEOUT = float(os.environ.get("CB_RESET_TIMEOUT", "10"))
CB_WINDOW_SECONDS = float(os.environ.get("CB_WINDOW_SECONDS", "120"))
if not (CB_FAILURE_THRESHOLD > 0 and 0 < CB_RESET_TIMEOUT < float("inf")
        and 0 < CB_WINDOW_SECONDS < float("inf")):
    raise ValueError("Circuit Breaker parameters must be positive finite numbers")
GRPC_API_KEY = os.environ["GRPC_API_KEY"]
if not GRPC_API_KEY or not GRPC_API_KEY.isascii() or not GRPC_API_KEY.isprintable():
    raise ValueError("GRPC_API_KEY must contain printable ASCII characters")
