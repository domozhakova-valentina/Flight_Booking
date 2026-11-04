"""Redis and UpdateFlight checks in a temporary PostgreSQL schema.

Run inside Flight Service: python - < tests/test_cache.py
"""

from datetime import datetime, timezone
import unittest
from unittest.mock import patch
from uuid import uuid4

from redis.exceptions import RedisError
from sqlalchemy import create_engine, text

from app import cache, db, service
from app.generated import flight_pb2 as p
from app.grpc_api import FlightService


class Context:
    def abort(self, code, details):
        raise AssertionError((code, details))


class CacheTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.original_engine = db.engine
        cls.schema = "cache_test_" + uuid4().hex
        with cls.original_engine.begin() as connection:
            connection.exec_driver_sql(f'CREATE SCHEMA "{cls.schema}"')
        cls.engine = create_engine(cls.original_engine.url,
            connect_args={"options": "-csearch_path=" + cls.schema})
        cls.addClassCleanup(cls.cleanup_schema)
        db.engine = service.engine = cls.engine
        cache.GENERATION_KEY = cls.schema + ":generation"
        db.migrate()

    @classmethod
    def cleanup_schema(cls):
        db.engine = service.engine = cls.original_engine
        cls.engine.dispose()
        with cls.original_engine.begin() as connection:
            connection.exec_driver_sql(f'DROP SCHEMA "{cls.schema}" CASCADE')
        cache.client.delete(cache.GENERATION_KEY)

    def setUp(self):
        self.flight_id = uuid4()
        self.api, self.context = FlightService(), Context()
        with self.engine.begin() as connection:
            connection.execute(text("TRUNCATE seat_reservations, flights"))
            connection.execute(text("""
                INSERT INTO flights (id, flight_number, airline, origin, destination,
                    departure_time, arrival_time, total_seats, available_seats, price_kopecks, status)
                VALUES (:id, 'TEST1', 'Test', 'SVO', 'LED', '2030-01-01T10:00:00Z',
                    '2030-01-01T12:00:00Z', 10, 10, 100, 'SCHEDULED')
            """), {"id": self.flight_id})
        cache.invalidate()

    def get_flight(self):
        return self.api.GetFlight(p.GetFlightRequest(flight_id=str(self.flight_id)), self.context)

    def search(self, origin="SVO", destination="LED", day=None):
        request = p.SearchFlightsRequest(origin=origin, destination=destination)
        if day:
            request.date.FromJsonString(day + "T00:00:00Z")
        return self.api.SearchFlights(request, self.context).flights

    def test_hit_miss_and_ttl(self):
        with patch.object(service, "get_flight", wraps=service.get_flight) as load, \
             self.assertLogs("app.cache", level="INFO") as logs:
            self.assertEqual(self.get_flight(), self.get_flight())
            self.assertEqual(load.call_count, 1)
        self.assertTrue(any("cache hit" in line for line in logs.output))
        self.assertTrue(any("cache miss" in line for line in logs.output))
        with patch.object(service, "search_flights", wraps=service.search_flights) as load:
            self.assertEqual(len(self.search()), 1)
            self.assertEqual(len(self.search()), 1)
            self.assertEqual(load.call_count, 1)
        for key in list(cache.client.scan_iter(match="*:" + cache.generation())) + [cache.GENERATION_KEY]:
            self.assertGreater(cache.client.ttl(key), 0)
            self.assertLessEqual(cache.client.ttl(key), 300)

    def test_reserve_and_release_invalidate_flight_and_search(self):
        self.get_flight()
        self.search()
        self.search(day="2030-01-01")
        booking_id = uuid4()
        service.reserve_seats(self.flight_id, 3, booking_id)
        self.assertEqual(self.get_flight().available_seats, 7)
        self.assertEqual(self.search()[0].available_seats, 7)
        self.assertEqual(self.search(day="2030-01-01")[0].available_seats, 7)
        service.release_reservation(booking_id)
        self.assertEqual(self.get_flight().available_seats, 10)
        self.assertEqual(self.search()[0].available_seats, 10)

    def test_update_partial_fields_route_and_capacity(self):
        self.get_flight()
        self.search()
        self.assertEqual(len(self.search("VKO", "AER")), 0)
        request = p.UpdateFlightRequest(flight_id=str(self.flight_id), origin="VKO",
            destination="AER", total_seats=12, price_kopecks=550)
        request.departure_time.FromDatetime(datetime(2030, 1, 2, 10, tzinfo=timezone.utc))
        request.arrival_time.FromDatetime(datetime(2030, 1, 2, 12, tzinfo=timezone.utc))
        result = self.api.UpdateFlight(request, self.context)
        self.assertEqual(result.flight_number, "TEST1")
        self.assertEqual(self.get_flight().price_kopecks, 550)
        self.assertEqual(len(self.search()), 0)
        self.assertEqual(len(self.search("VKO", "AER", "2030-01-02")), 1)
        self.assertEqual(len(self.search("VKO", "AER", "2030-01-01")), 0)
        booking_id = uuid4()
        service.reserve_seats(self.flight_id, 4, booking_id)
        with self.assertRaises(service.FlightError) as caught:
            service.update_flight(self.flight_id, {"total_seats": 3, "price_kopecks": 999})
        self.assertEqual(caught.exception.code, "FAILED_PRECONDITION")
        self.assertEqual(self.get_flight().price_kopecks, 550)
        updated = service.update_flight(self.flight_id, {"total_seats": 7})
        self.assertEqual(updated["available_seats"], 3)
        service.update_flight(self.flight_id, {"status": "CANCELLED"})
        self.assertEqual(len(self.search("VKO", "AER")), 0)
        with self.assertRaises(service.FlightError):
            service.update_flight(self.flight_id, {"price_kopecks": 0})

    def test_late_reader_cannot_restore_old_cache(self):
        old = self.get_flight()
        old_token = cache.generation()
        service.update_flight(self.flight_id, {"price_kopecks": 200})
        cache.put(old_token, "flight:" + str(self.flight_id), old)
        self.assertEqual(self.get_flight().price_kopecks, 200)

    def test_redis_failure_bypasses_cache_and_recovers(self):
        self.get_flight()
        with patch.object(cache.client, "set", side_effect=RedisError("test outage")):
            service.update_flight(self.flight_id, {"price_kopecks": 300})
            self.assertEqual(self.get_flight().price_kopecks, 300)
        self.assertEqual(self.get_flight().price_kopecks, 300)


if __name__ == "__main__":
    unittest.main(verbosity=2)
