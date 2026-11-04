"""Запуск gRPC-сервера Flight Service."""

from concurrent.futures import ThreadPoolExecutor
import logging
import signal

import grpc

from . import cache
from .db import engine, migrate
from .generated import flight_pb2_grpc
from .grpc_api import ApiKeyInterceptor, FlightService


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s %(message)s")
    migrate()
    print("Flight DB: migrations applied", flush=True)
    server = grpc.server(ThreadPoolExecutor(max_workers=10), interceptors=[ApiKeyInterceptor()])
    flight_pb2_grpc.add_FlightServiceServicer_to_server(
        FlightService(), server,
    )
    server.add_insecure_port("0.0.0.0:50051")
    signal.signal(signal.SIGTERM, lambda *_: server.stop(5))
    signal.signal(signal.SIGINT, lambda *_: server.stop(5))
    server.start()
    print("Flight Service: listening on :50051", flush=True)
    try:
        server.wait_for_termination()
    finally:
        server.stop(5).wait()
        cache.client.close()
        engine.dispose()


if __name__ == "__main__":
    main()
