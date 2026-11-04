"""gRPC-методы Flight Service, кеширование чтения и преобразование ошибок."""

from datetime import timezone
from hmac import compare_digest
import re
from uuid import UUID

import grpc
from google.protobuf.timestamp_pb2 import Timestamp
from sqlalchemy.exc import SQLAlchemyError

from . import cache, service
from .config import GRPC_API_KEY
from .generated import flight_pb2, flight_pb2_grpc


class ApiKeyInterceptor(grpc.ServerInterceptor):
    """Одна проверка ключа перед любым методом Flight Service."""

    def intercept_service(self, continuation, handler_call_details):
        metadata = dict(handler_call_details.invocation_metadata or ())
        if not compare_digest(metadata.get("x-api-key", ""), GRPC_API_KEY):
            return grpc.unary_unary_rpc_method_handler(self.reject)
        return continuation(handler_call_details)

    @staticmethod
    def reject(request, context):
        context.abort(grpc.StatusCode.UNAUTHENTICATED, "Invalid API key")


def _flight_message(row):
    departure = Timestamp()
    departure.FromDatetime(row["departure_time"])
    arrival = Timestamp()
    arrival.FromDatetime(row["arrival_time"])
    return flight_pb2.Flight(
        id=str(row["id"]),
        flight_number=row["flight_number"],
        airline=row["airline"],
        origin=row["origin"],
        destination=row["destination"],
        departure_time=departure,
        arrival_time=arrival,
        total_seats=row["total_seats"],
        available_seats=row["available_seats"],
        price_kopecks=row["price_kopecks"],
        status=flight_pb2.FlightStatus.Value("FLIGHT_STATUS_" + row["status"]),
    )


def _reservation_message(row):
    return flight_pb2.SeatReservation(
        booking_id=str(row["booking_id"]),
        flight_id=str(row["flight_id"]),
        seat_count=row["seat_count"],
        status=flight_pb2.ReservationStatus.Value("RESERVATION_STATUS_" + row["status"]),
    )


class FlightService(flight_pb2_grpc.FlightServiceServicer):
    def SearchFlights(self, request, context):
        if (not re.fullmatch(r"[A-Z]{3}", request.origin)
                or not re.fullmatch(r"[A-Z]{3}", request.destination)
                or request.origin == request.destination):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid airport codes")

        date = None
        if request.HasField("date"):
            try:
                value = request.date.ToDatetime(tzinfo=timezone.utc)
            except (ValueError, OverflowError):
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid date")
            if value.hour or value.minute or value.second or request.date.nanos:
                context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Date must be midnight UTC")
            date = value.date()

        token = cache.generation()
        key = f"search:{request.origin}:{request.destination}:{date.isoformat() if date else 'all'}"
        cached = cache.get(token, key, flight_pb2.SearchFlightsResponse)
        if cached is not None:
            return cached
        try:
            rows = service.search_flights(request.origin, request.destination, date)
        except SQLAlchemyError:
            context.abort(grpc.StatusCode.INTERNAL, "Database error")
        result = flight_pb2.SearchFlightsResponse(flights=[_flight_message(row) for row in rows])
        cache.put(token, key, result)
        return result

    def GetFlight(self, request, context):
        try:
            flight_id = UUID(request.flight_id)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid flight ID")

        token = cache.generation()
        key = f"flight:{flight_id}"
        cached = cache.get(token, key, flight_pb2.Flight)
        if cached is not None:
            return cached
        try:
            row = service.get_flight(flight_id)
        except SQLAlchemyError:
            context.abort(grpc.StatusCode.INTERNAL, "Database error")
        if row is None:
            context.abort(grpc.StatusCode.NOT_FOUND, "Flight not found")
        result = _flight_message(row)
        cache.put(token, key, result)
        return result

    def ReserveSeats(self, request, context):
        try:
            flight_id = UUID(request.flight_id)
            booking_id = UUID(request.booking_id)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid flight or booking ID")
        if request.seat_count <= 0:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Seat count must be positive")
        try:
            row = service.reserve_seats(flight_id, request.seat_count, booking_id)
        except service.FlightError as error:
            context.abort(grpc.StatusCode[error.code], str(error))
        except SQLAlchemyError:
            context.abort(grpc.StatusCode.INTERNAL, "Database error")
        return _reservation_message(row)

    def ReleaseReservation(self, request, context):
        try:
            booking_id = UUID(request.booking_id)
        except ValueError:
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid booking ID")
        try:
            row = service.release_reservation(booking_id)
        except service.FlightError as error:
            context.abort(grpc.StatusCode[error.code], str(error))
        except SQLAlchemyError:
            context.abort(grpc.StatusCode.INTERNAL, "Database error")
        return _reservation_message(row)

    def UpdateFlight(self, request, context):
        try:
            flight_id = UUID(request.flight_id)
            changes = {}
            for name in ("flight_number", "airline", "origin", "destination", "total_seats", "price_kopecks"):
                if request.HasField(name):
                    changes[name] = getattr(request, name)
            for name in ("departure_time", "arrival_time"):
                if request.HasField(name):
                    changes[name] = getattr(request, name).ToDatetime(tzinfo=timezone.utc)
            if request.HasField("status"):
                changes["status"] = flight_pb2.FlightStatus.Name(request.status).removeprefix("FLIGHT_STATUS_")
        except (ValueError, OverflowError):
            context.abort(grpc.StatusCode.INVALID_ARGUMENT, "Invalid flight ID, timestamp or status")
        try:
            row = service.update_flight(flight_id, changes)
        except service.FlightError as error:
            context.abort(grpc.StatusCode[error.code], str(error))
        except SQLAlchemyError:
            context.abort(grpc.StatusCode.INTERNAL, "Database error")
        return _flight_message(row)
