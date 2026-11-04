"""Point 5 checks on PostgreSQL in an isolated temporary schema.

Run inside the matching service: python - flight or python - booking.
"""

from concurrent.futures import ThreadPoolExecutor
import sys
from threading import Barrier
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from uuid import uuid4

import grpc
from sqlalchemy import create_engine, text
from sqlalchemy.exc import IntegrityError

from app import db, service


class SchemaCase(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_engine = db.engine
        cls.schema = "tx_test_" + uuid4().hex
        with cls.original_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.test_engine = create_engine(
            cls.original_engine.url,
            connect_args={"options": "-csearch_path=" + cls.schema},
        )
        cls.addClassCleanup(cls.cleanup_schema)
        db.engine = service.engine = cls.test_engine
        db.migrate()

    @classmethod
    def cleanup_schema(cls):
        db.engine = service.engine = cls.original_engine
        cls.test_engine.dispose()
        with cls.original_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')

    def execute(self, sql, parameters=None):
        with self.test_engine.begin() as connection:
            connection.execute(text(sql), parameters or {})

    def scalar(self, sql, parameters=None):
        with self.test_engine.connect() as connection:
            return connection.execute(text(sql), parameters or {}).scalar_one()


class FlightTransactions(SchemaCase):
    def setUp(self):
        self.execute("TRUNCATE seat_reservations, flights")

    def add_flight(self, number="TEST1"):
        flight_id = uuid4()
        self.execute("""
            INSERT INTO flights
            (id, flight_number, airline, origin, destination, departure_time,
             arrival_time, total_seats, available_seats, price_kopecks, status)
            VALUES (:id, :number, 'Test', 'SVO', 'LED', '2030-01-01T10:00:00Z',
                    '2030-01-01T12:00:00Z', 1, 1, 100, 'SCHEDULED')
        """, {"id": flight_id, "number": number})
        return flight_id

    def available(self):
        return self.scalar("SELECT available_seats FROM flights")

    def reject_reservations(self, condition):
        self.execute("ALTER TABLE seat_reservations ADD CONSTRAINT test_guard CHECK (" + condition + ")")
        self.addCleanup(self.execute, "ALTER TABLE seat_reservations DROP CONSTRAINT test_guard")

    def test_last_seat(self):
        flight_id = self.add_flight()
        barrier = Barrier(2)

        def reserve(booking_id):
            barrier.wait(timeout=5)
            try:
                service.reserve_seats(flight_id, 1, booking_id)
                return "OK"
            except service.FlightError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, [uuid4(), uuid4()]))
        self.assertCountEqual(results, ["OK", "RESOURCE_EXHAUSTED"])
        self.assertEqual(self.available(), 0)
        self.assertEqual(self.scalar("SELECT count(*) FROM seat_reservations"), 1)

    def test_failed_insert_restores_seats(self):
        flight_id = self.add_flight()
        self.reject_reservations("false")
        with self.assertRaises(IntegrityError):
            service.reserve_seats(flight_id, 1, uuid4())
        self.assertEqual(self.available(), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM seat_reservations"), 0)

    def test_concurrent_retries_return_same_reservation(self):
        flight_id, booking_id = self.add_flight(), uuid4()
        barrier = Barrier(2)

        def reserve(_):
            barrier.wait(timeout=5)
            return service.reserve_seats(flight_id, 1, booking_id)

        with ThreadPoolExecutor(max_workers=2) as pool:
            first, second = list(pool.map(reserve, range(2)))
        self.assertEqual(first, second)
        self.assertEqual(service.reserve_seats(flight_id, 1, booking_id), first)
        self.assertEqual(self.available(), 0)
        self.assertEqual(self.scalar("SELECT count(*) FROM seat_reservations"), 1)
        with self.assertRaises(service.FlightError) as caught:
            service.reserve_seats(flight_id, 2, booking_id)
        self.assertEqual(caught.exception.code, "ALREADY_EXISTS")
        self.assertEqual(self.available(), 0)

    def test_same_booking_for_different_flights_rolls_back_loser(self):
        flights = [self.add_flight("TEST1"), self.add_flight("TEST2")]
        booking_id, barrier = uuid4(), Barrier(2)

        def reserve(flight_id):
            barrier.wait(timeout=5)
            try:
                service.reserve_seats(flight_id, 1, booking_id)
                return "OK"
            except service.FlightError as error:
                return error.code

        with ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(reserve, flights))
        self.assertCountEqual(results, ["OK", "ALREADY_EXISTS"])
        self.assertEqual(self.scalar("SELECT sum(available_seats) FROM flights"), 1)
        self.assertEqual(self.scalar("SELECT count(*) FROM seat_reservations"), 1)

    def test_failed_status_update_does_not_return_seats(self):
        flight_id, booking_id = self.add_flight(), uuid4()
        service.reserve_seats(flight_id, 1, booking_id)
        self.reject_reservations("status = 'ACTIVE'")
        with self.assertRaises(IntegrityError):
            service.release_reservation(booking_id)
        self.assertEqual(self.available(), 0)
        self.assertEqual(self.scalar("SELECT status FROM seat_reservations"), "ACTIVE")

    def test_release_returns_seats_once(self):
        flight_id, booking_id = self.add_flight(), uuid4()
        service.reserve_seats(flight_id, 1, booking_id)
        service.release_reservation(booking_id)
        service.release_reservation(booking_id)
        self.assertEqual(self.available(), 1)
        self.assertEqual(self.scalar("SELECT status FROM seat_reservations"), "RELEASED")


class ReserveFailure(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.RESOURCE_EXHAUSTED


class BookingTransactions(SchemaCase):
    def setUp(self):
        self.execute("TRUNCATE bookings")
        self.data = dict(user_id=uuid4(), flight_id=uuid4(), passenger_name="Test",
                         passenger_email="test@example.com", seat_count=1)

    def test_grpc_error_does_not_create_booking(self):
        with patch.object(service.flight_client, "get_flight", return_value=SimpleNamespace(price_kopecks=100)), \
             patch.object(service.flight_client, "reserve_seats", side_effect=ReserveFailure()), \
             patch.object(service.flight_client, "release_reservation") as release:
            with self.assertRaises(ReserveFailure):
                service.create_booking(self.data)
            release.assert_not_called()
        self.assertEqual(self.scalar("SELECT count(*) FROM bookings"), 0)

    def test_failed_save_rolls_back_booking_and_requests_release(self):
        # An empty name triggers a real database CHECK after reservation succeeds.
        with patch.object(service.flight_client, "get_flight", return_value=SimpleNamespace(price_kopecks=100)), \
             patch.object(service.flight_client, "reserve_seats") as reserve, \
             patch.object(service.flight_client, "release_reservation") as release:
            with self.assertRaises(IntegrityError):
                service.create_booking(dict(self.data, passenger_name=""))
            release.assert_called_once_with(reserve.call_args.args[2])
        self.assertEqual(self.scalar("SELECT count(*) FROM bookings"), 0)


if __name__ == "__main__":
    cases = {"flight": FlightTransactions, "booking": BookingTransactions}
    if len(sys.argv) != 2 or sys.argv[1] not in cases:
        raise SystemExit("Specify flight or booking")
    suite = unittest.defaultTestLoader.loadTestsFromTestCase(cases[sys.argv[1]])
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(not result.wasSuccessful())
