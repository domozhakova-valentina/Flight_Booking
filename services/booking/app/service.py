"""Сценарии бронирования и отмены, фиксация цены и согласование с Flight Service."""

import logging
from uuid import uuid4

import grpc
from sqlalchemy import text
from sqlalchemy.exc import SQLAlchemyError

from . import flight_client
from .db import engine


logger = logging.getLogger(__name__)


def _release_after_failure(booking_id):
    try:
        flight_client.release_reservation(booking_id)
    except grpc.RpcError:
        # Не скрываем первоначальную ошибку; ID нужен для ручного восстановления.
        logger.exception("Could not release reservation for booking %s", booking_id)


def create_booking(data):
    booking_id = uuid4()
    flight = flight_client.get_flight(data["flight_id"])
    total = data["seat_count"] * flight.price_kopecks
    if total <= 0 or total > 2**63 - 1:
        raise ValueError("Total price is outside the supported range")

    try:
        # До успешного резервирования запись в Booking DB не создаётся.
        flight_client.reserve_seats(data["flight_id"], data["seat_count"], booking_id)
    except grpc.RpcError as error:
        # При потере ответа операция на стороне Flight могла успеть выполниться.
        if error.code() in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
            _release_after_failure(booking_id)
        raise

    try:
        with engine.begin() as connection:
            row = connection.execute(text("""
                INSERT INTO bookings
                (id, user_id, flight_id, passenger_name, passenger_email,
                 seat_count, total_price_kopecks, status)
                VALUES (:id, :user_id, :flight_id, :passenger_name, :passenger_email,
                        :seat_count, :total, 'CONFIRMED')
                RETURNING *
            """), dict(data, id=booking_id, total=total)).mappings().one()
            booking = dict(row)
    except SQLAlchemyError:
        _release_after_failure(booking_id)
        raise
    return booking


def get_booking(booking_id):
    with engine.connect() as connection:
        row = connection.execute(text(
            "SELECT * FROM bookings WHERE id = :id"
        ), {"id": booking_id}).mappings().first()
        return dict(row) if row is not None else None


def list_bookings(user_id):
    with engine.connect() as connection:
        rows = connection.execute(text(
            "SELECT * FROM bookings WHERE user_id = :user_id ORDER BY id"
        ), {"user_id": user_id}).mappings().all()
        return [dict(row) for row in rows]


def cancel_booking(booking_id):
    with engine.begin() as connection:
        booking = connection.execute(text(
            "SELECT * FROM bookings WHERE id = :id FOR UPDATE"
        ), {"id": booking_id}).mappings().first()
        if booking is None:
            raise LookupError("Booking not found")
        if booking["status"] == "CANCELLED":
            return dict(booking)
        flight_client.release_reservation(booking_id)
        row = connection.execute(text("""
            UPDATE bookings SET status = 'CANCELLED'
            WHERE id = :id RETURNING *
        """), {"id": booking_id}).mappings().one()
        return dict(row)
