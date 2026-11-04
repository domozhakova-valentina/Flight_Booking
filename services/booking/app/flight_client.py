"""Вызовы Flight Service через один переиспользуемый gRPC-канал."""

from datetime import datetime, time, timezone
from time import sleep

import grpc

from .circuit_breaker import CircuitBreaker, UNAVAILABLE_CODES
from .config import (
    CB_FAILURE_THRESHOLD, CB_RESET_TIMEOUT, CB_WINDOW_SECONDS,
    FLIGHT_SERVICE_ADDRESS, GRPC_API_KEY,
)
from .generated import flight_pb2, flight_pb2_grpc


channel = grpc.insecure_channel(
    FLIGHT_SERVICE_ADDRESS, options=(("grpc.enable_retries", 0),),
)
stub = flight_pb2_grpc.FlightServiceStub(channel)
metadata = (("x-api-key", GRPC_API_KEY),)
breaker = CircuitBreaker(CB_FAILURE_THRESHOLD, CB_RESET_TIMEOUT, CB_WINDOW_SECONDS)


def _call(method, request):
    # Общий клиентский middleware, вне бизнес-логики бронирования.
    return breaker.call(_call_with_retry, method, request)


def _call_with_retry(method, request):
    """Не более трёх попыток; один и тот же запрос на каждой попытке."""
    for attempt in range(3):
        try:
            return method(request, timeout=5, metadata=metadata)
        except grpc.RpcError as error:
            if attempt == 2 or error.code() not in UNAVAILABLE_CODES:
                raise
            sleep(0.1 * (2 ** attempt))


def search_flights(origin, destination, date=None):
    request = flight_pb2.SearchFlightsRequest(origin=origin, destination=destination)
    if date is not None:
        request.date.FromDatetime(datetime.combine(date, time.min, tzinfo=timezone.utc))
    return _call(stub.SearchFlights, request).flights


def get_flight(flight_id):
    return _call(stub.GetFlight, flight_pb2.GetFlightRequest(flight_id=str(flight_id)))


def reserve_seats(flight_id, seat_count, booking_id):
    return _call(stub.ReserveSeats, flight_pb2.ReserveSeatsRequest(
        flight_id=str(flight_id), seat_count=seat_count, booking_id=str(booking_id),
    ))


def release_reservation(booking_id):
    return _call(
        stub.ReleaseReservation, flight_pb2.ReleaseReservationRequest(booking_id=str(booking_id)),
    )
