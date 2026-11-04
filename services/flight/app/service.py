"""Рейсы и транзакционные операции с местами в Flight DB."""

import re

from sqlalchemy import text
from sqlalchemy.exc import IntegrityError

from . import cache
from .db import engine


class FlightError(Exception):
    def __init__(self, code, message):
        super().__init__(message)
        self.code = code


def search_flights(origin, destination, date=None):
    sql = """
        SELECT * FROM flights
        WHERE origin = :origin AND destination = :destination
          AND status = 'SCHEDULED'
    """
    parameters = {"origin": origin, "destination": destination}
    if date is not None:
        sql += " AND (departure_time AT TIME ZONE 'UTC')::date = :date"
        parameters["date"] = date
    sql += " ORDER BY departure_time, id"
    with engine.connect() as connection:
        return connection.execute(text(sql), parameters).mappings().all()


def get_flight(flight_id):
    with engine.connect() as connection:
        return connection.execute(
            text("SELECT * FROM flights WHERE id = :id"), {"id": flight_id},
        ).mappings().first()


def reserve_seats(flight_id, seat_count, booking_id):
    """Списание мест и создание резервации фиксируются или откатываются вместе."""
    with engine.begin() as connection:
        flight = connection.execute(text(
            "SELECT * FROM flights WHERE id = :id FOR UPDATE"
        ), {"id": flight_id}).mappings().first()
        if flight is None:
            raise FlightError("NOT_FOUND", "Flight not found")

        existing = connection.execute(text(
            "SELECT * FROM seat_reservations WHERE booking_id = :id"
        ), {"id": booking_id}).mappings().first()
        if existing is not None:
            if existing["flight_id"] != flight_id or existing["seat_count"] != seat_count:
                raise FlightError("ALREADY_EXISTS", "Booking ID has different reservation parameters")
            if existing["status"] != "ACTIVE":
                raise FlightError("FAILED_PRECONDITION", "Reservation is no longer active")
            return dict(existing)

        if flight["status"] != "SCHEDULED":
            raise FlightError("FAILED_PRECONDITION", "Flight is not scheduled")
        if flight["available_seats"] < seat_count:
            raise FlightError("RESOURCE_EXHAUSTED", "Not enough seats")

        connection.execute(text(
            "UPDATE flights SET available_seats = available_seats - :count WHERE id = :id"
        ), {"count": seat_count, "id": flight_id})
        reservation = connection.execute(text("""
            INSERT INTO seat_reservations (booking_id, flight_id, seat_count, status)
            VALUES (:booking, :flight, :count, 'ACTIVE')
            ON CONFLICT (booking_id) DO NOTHING
            RETURNING *
        """), {"booking": booking_id, "flight": flight_id, "count": seat_count}).mappings().first()
        if reservation is None:
            # Конкурирующий запрос с этим booking_id мог резервировать другой рейс.
            # Исключение откатит и уменьшение available_seats.
            raise FlightError("ALREADY_EXISTS", "Booking ID already has a reservation")
        result = dict(reservation)
    # COMMIT уже выполнен: кеш не должен отражать незавершённую транзакцию.
    cache.invalidate()
    return result


def release_reservation(booking_id):
    """Возврат мест и переход в RELEASED выполняются в одной транзакции."""
    with engine.begin() as connection:
        reservation = connection.execute(text(
            "SELECT * FROM seat_reservations WHERE booking_id = :id"
        ), {"id": booking_id}).mappings().first()
        if reservation is None:
            raise FlightError("NOT_FOUND", "Reservation not found")

        # Во всех операциях сначала блокируем рейс, затем резервацию.
        connection.execute(text(
            "SELECT id FROM flights WHERE id = :id FOR UPDATE"
        ), {"id": reservation["flight_id"]})
        reservation = connection.execute(text(
            "SELECT * FROM seat_reservations WHERE booking_id = :id FOR UPDATE"
        ), {"id": booking_id}).mappings().one()
        if reservation["status"] != "ACTIVE":
            return dict(reservation)

        connection.execute(text(
            "UPDATE flights SET available_seats = available_seats + :count WHERE id = :id"
        ), {"count": reservation["seat_count"], "id": reservation["flight_id"]})
        row = connection.execute(text("""
            UPDATE seat_reservations SET status = 'RELEASED'
            WHERE booking_id = :id RETURNING *
        """), {"id": booking_id}).mappings().one()
        result = dict(row)
    cache.invalidate()
    return result


def update_flight(flight_id, changes):
    allowed = {"flight_number", "airline", "origin", "destination", "departure_time",
               "arrival_time", "total_seats", "price_kopecks", "status"}
    if not changes or not changes.keys() <= allowed:
        raise FlightError("INVALID_ARGUMENT", "Specify flight fields to update")
    try:
        with engine.begin() as connection:
            old = connection.execute(text(
                "SELECT * FROM flights WHERE id = :id FOR UPDATE"
            ), {"id": flight_id}).mappings().first()
            if old is None:
                raise FlightError("NOT_FOUND", "Flight not found")
            updated = dict(old, **changes)
            updated["flight_number"] = updated["flight_number"].strip()
            updated["airline"] = updated["airline"].strip()
            if not updated["flight_number"] or len(updated["flight_number"]) > 16 or not updated["airline"]:
                raise FlightError("INVALID_ARGUMENT", "Invalid flight number or airline")
            if (not re.fullmatch(r"[A-Z]{3}", updated["origin"])
                    or not re.fullmatch(r"[A-Z]{3}", updated["destination"])
                    or updated["origin"] == updated["destination"]):
                raise FlightError("INVALID_ARGUMENT", "Invalid airport codes")
            if updated["arrival_time"] <= updated["departure_time"]:
                raise FlightError("INVALID_ARGUMENT", "Arrival must be after departure")
            if updated["total_seats"] <= 0 or updated["price_kopecks"] <= 0:
                raise FlightError("INVALID_ARGUMENT", "Capacity and price must be positive")
            if updated["status"] not in {"SCHEDULED", "DEPARTED", "CANCELLED", "COMPLETED"}:
                raise FlightError("INVALID_ARGUMENT", "Invalid flight status")

            occupied = old["total_seats"] - old["available_seats"]
            if updated["total_seats"] < occupied:
                raise FlightError("FAILED_PRECONDITION", "Capacity is below the number of reserved seats")
            updated["available_seats"] = updated["total_seats"] - occupied
            row = connection.execute(text("""
                UPDATE flights SET flight_number = :flight_number, airline = :airline,
                    origin = :origin, destination = :destination,
                    departure_time = :departure_time, arrival_time = :arrival_time,
                    total_seats = :total_seats, available_seats = :available_seats,
                    price_kopecks = :price_kopecks, status = :status
                WHERE id = :id RETURNING *
            """), updated).mappings().one()
            result = dict(row)
    except IntegrityError as error:
        if getattr(error.orig, "sqlstate", None) == "23505":
            raise FlightError("ALREADY_EXISTS", "Flight number and departure date already exist") from error
        raise
    cache.invalidate()
    return result
