"""Client middleware checks without real outages or waiting."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
import unittest
from unittest.mock import Mock, patch

import grpc

from app import flight_client
from app.api import _http_error
from app.circuit_breaker import CircuitBreaker, CircuitOpen


class RpcFailure(grpc.RpcError):
    def __init__(self, code):
        self.status = code

    def code(self):
        return self.status


class CircuitTests(unittest.TestCase):
    def setUp(self):
        self.now = 0
        clock = patch("app.circuit_breaker.monotonic", side_effect=lambda: self.now)
        clock.start()
        self.addCleanup(clock.stop)
        self.breaker = CircuitBreaker(2, 10, 60)
        self.failure = Mock(side_effect=RpcFailure(grpc.StatusCode.UNAVAILABLE))

    def fail_call(self):
        with self.assertRaises(RpcFailure):
            self.breaker.call(self.failure)

    def test_open_rejects_without_call_and_recovers(self):
        with self.assertLogs("app.circuit_breaker", level="INFO") as logs:
            self.fail_call()
            self.fail_call()
            operation = Mock(return_value="OK")
            with self.assertRaises(CircuitOpen) as caught:
                self.breaker.call(operation)
            operation.assert_not_called()
            http = _http_error(caught.exception)
            self.assertEqual(http.status_code, 503)
            self.assertIn("circuit breaker", http.detail)
            self.now = 10
            self.assertEqual(self.breaker.call(operation), "OK")
        self.assertEqual(self.breaker.state, "CLOSED")
        for transition in ("CLOSED -> OPEN", "OPEN -> HALF_OPEN", "HALF_OPEN -> CLOSED"):
            self.assertTrue(any(transition in line for line in logs.output))

    def test_failed_probe_opens_again(self):
        self.fail_call()
        self.fail_call()
        self.now = 10
        self.fail_call()
        self.assertEqual(self.breaker.state, "OPEN")
        self.now = 19
        with self.assertRaises(CircuitOpen):
            self.breaker.call(Mock())
        self.now = 20
        self.assertEqual(self.breaker.call(lambda: "OK"), "OK")

    def test_window_success_and_business_error_reset_failures(self):
        self.fail_call()
        self.now = 61
        self.fail_call()
        self.assertEqual(self.breaker.state, "CLOSED")
        self.breaker.call(lambda: "OK")
        self.fail_call()
        with self.assertRaises(RpcFailure):
            self.breaker.call(Mock(side_effect=RpcFailure(grpc.StatusCode.RESOURCE_EXHAUSTED)))
        self.fail_call()
        self.assertEqual(self.breaker.state, "CLOSED")

    def test_only_one_probe_and_late_success_does_not_close_open(self):
        entered, finish = Event(), Event()

        def slow():
            entered.set()
            if not finish.wait(timeout=5):
                raise AssertionError("Test worker timed out")
            return "OK"

        with ThreadPoolExecutor(max_workers=1) as pool:
            late = pool.submit(self.breaker.call, slow)
            try:
                self.assertTrue(entered.wait(timeout=5))
                self.fail_call()
                self.fail_call()
            finally:
                finish.set()
            self.assertEqual(late.result(timeout=5), "OK")
            self.assertEqual(self.breaker.state, "OPEN")
            self.now = 10
            entered.clear()
            finish.clear()
            probe = pool.submit(self.breaker.call, slow)
            try:
                self.assertTrue(entered.wait(timeout=5))
                other = Mock()
                with self.assertRaises(CircuitOpen):
                    self.breaker.call(other)
                other.assert_not_called()
            finally:
                finish.set()
            self.assertEqual(probe.result(timeout=5), "OK")
            self.assertEqual(self.breaker.state, "CLOSED")

    def test_retry_sequence_counts_as_one_failure(self):
        with patch.object(flight_client, "breaker", self.breaker), \
             patch.object(flight_client, "sleep"):
            method = Mock(side_effect=RpcFailure(grpc.StatusCode.DEADLINE_EXCEEDED))
            for _ in range(2):
                with self.assertRaises(RpcFailure):
                    flight_client._call(method, object())
            self.assertEqual(method.call_count, 6)
            with self.assertRaises(CircuitOpen):
                flight_client._call(method, object())
            self.assertEqual(method.call_count, 6)


if __name__ == "__main__":
    unittest.main(verbosity=2)
