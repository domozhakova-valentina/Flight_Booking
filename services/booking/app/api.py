"""REST API рейсов и бронирований."""

from datetime import date as Date
from uuid import UUID

from fastapi import APIRouter, HTTPException, Query
import grpc
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from . import flight_client, service
from .circuit_breaker import CircuitOpen
from .generated import flight_pb2


router = APIRouter()


class BookingCreate(BaseModel):
    model_config = ConfigDict(str_strip_whitespace=True)

    user_id: UUID
    flight_id: UUID
    passenger_name: str = Field(min_length=1)
    passenger_email: str = Field(max_length=254, pattern=r"^[^@\s]+@[^@\s]+\.[^@\s]+$")
    seat_count: int = Field(gt=0, le=2**31 - 1, strict=True)


def _flight_dict(flight):
    return {
        "id": flight.id,
        "flight_number": flight.flight_number,
        "airline": flight.airline,
        "origin": flight.origin,
        "destination": flight.destination,
        "departure_time": flight.departure_time.ToJsonString(),
        "arrival_time": flight.arrival_time.ToJsonString(),
        "total_seats": flight.total_seats,
        "available_seats": flight.available_seats,
        "price_kopecks": flight.price_kopecks,
        "status": flight_pb2.FlightStatus.Name(flight.status).removeprefix("FLIGHT_STATUS_"),
    }


def _http_error(error):
    if isinstance(error, CircuitOpen):
        return HTTPException(503, error.details())
    if error.code() == grpc.StatusCode.NOT_FOUND:
        return HTTPException(404, error.details())
    if error.code() == grpc.StatusCode.INVALID_ARGUMENT:
        return HTTPException(400, error.details())
    if error.code() in (grpc.StatusCode.RESOURCE_EXHAUSTED,
                        grpc.StatusCode.FAILED_PRECONDITION, grpc.StatusCode.ALREADY_EXISTS):
        return HTTPException(409, error.details())
    if error.code() == grpc.StatusCode.UNAVAILABLE:
        return HTTPException(503, "Flight Service is unavailable")
    if error.code() == grpc.StatusCode.DEADLINE_EXCEEDED:
        return HTTPException(504, "Flight Service timeout")
    return HTTPException(502, "Flight Service error")


@router.get("/flights")
def search_flights(
    origin: str = Query(..., pattern=r"^[A-Z]{3}$"),
    destination: str = Query(..., pattern=r"^[A-Z]{3}$"),
    date: Date | None = None,
):
    try:
        flights = flight_client.search_flights(origin, destination, date)
    except grpc.RpcError as error:
        raise _http_error(error) from error
    return [_flight_dict(flight) for flight in flights]


@router.get("/flights/{id}")
def get_flight(id: UUID):
    try:
        flight = flight_client.get_flight(id)
    except grpc.RpcError as error:
        raise _http_error(error) from error
    return _flight_dict(flight)


@router.post("/bookings", status_code=201)
def create_booking(data: BookingCreate):
    try:
        return service.create_booking(data.model_dump())
    except grpc.RpcError as error:
        raise _http_error(error) from error
    except ValueError as error:
        raise HTTPException(400, str(error)) from error
    except SQLAlchemyError as error:
        raise HTTPException(500, "Could not save booking") from error


@router.get("/bookings")
def list_bookings(user_id: UUID):
    return service.list_bookings(user_id)


@router.get("/bookings/{id}")
def get_booking(id: UUID):
    booking = service.get_booking(id)
    if booking is None:
        raise HTTPException(404, "Booking not found")
    return booking


@router.post("/bookings/{id}/cancel")
def cancel_booking(id: UUID):
    try:
        return service.cancel_booking(id)
    except LookupError as error:
        raise HTTPException(404, str(error)) from error
    except grpc.RpcError as error:
        raise _http_error(error) from error
    except SQLAlchemyError as error:
        raise HTTPException(500, "Could not cancel booking") from error
