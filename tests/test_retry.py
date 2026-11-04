"""Booking gRPC retry checks; no database changes or real delays."""

from types import SimpleNamespace
import unittest
from unittest.mock import Mock, call, patch

import grpc

from app import flight_client


class RpcFailure(grpc.RpcError):
    def __init__(self, status):
        self.status = status

    def code(self):
        return self.status


class RetryTests(unittest.TestCase):
    def setUp(self):
        breaker = flight_client.CircuitBreaker(5, 10, 60)
        replacement = patch.object(flight_client, "breaker", breaker)
        replacement.start()
        self.addCleanup(replacement.stop)

    def test_all_client_methods_retry_with_same_request(self):
        operations = [
            ("SearchFlights", lambda: flight_client.search_flights("SVO", "LED")),
            ("GetFlight", lambda: flight_client.get_flight("flight-id")),
            ("ReserveSeats", lambda: flight_client.reserve_seats("flight-id", 2, "booking-id")),
            ("ReleaseReservation", lambda: flight_client.release_reservation("booking-id")),
        ]
        response = SimpleNamespace(flights=[])
        for name, invoke in operations:
            with self.subTest(method=name), \
                 patch.object(flight_client.stub, name, side_effect=[
                     RpcFailure(grpc.StatusCode.UNAVAILABLE),
                     RpcFailure(grpc.StatusCode.DEADLINE_EXCEEDED), response,
                 ]) as method, patch.object(flight_client, "sleep") as sleep:
                invoke()
                self.assertEqual(method.call_count, 3)
                first = method.call_args_list[0]
                for attempt in method.call_args_list:
                    self.assertIs(attempt.args[0], first.args[0])
                    self.assertEqual(attempt.kwargs, dict(timeout=5, metadata=flight_client.metadata))
                if name == "ReserveSeats":
                    self.assertEqual(first.args[0].booking_id, "booking-id")
                self.assertEqual(sleep.call_args_list, [call(0.1), call(0.2)])

    def test_stops_after_third_failure(self):
        for code in (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
            failure = RpcFailure(code)
            with self.subTest(code=code), patch.object(flight_client, "sleep") as sleep:
                method = Mock(side_effect=failure)
                with self.assertRaises(RpcFailure) as caught:
                    flight_client._call(method, object())
                self.assertIs(caught.exception, failure)
                self.assertEqual(method.call_count, 3)
                self.assertEqual(sleep.call_count, 2)

    def test_other_errors_and_success_are_not_retried(self):
        for code in grpc.StatusCode:
            if code in (grpc.StatusCode.OK, grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED):
                continue
            with self.subTest(code=code), patch.object(flight_client, "sleep") as sleep:
                method = Mock(side_effect=RpcFailure(code))
                with self.assertRaises(RpcFailure):
                    flight_client._call(method, object())
                method.assert_called_once()
                sleep.assert_not_called()
        with patch.object(flight_client, "sleep") as sleep:
            method = Mock(return_value="result")
            self.assertEqual(flight_client._call(method, object()), "result")
            method.assert_called_once()
            sleep.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
