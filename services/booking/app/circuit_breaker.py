"""Потокобезопасный middleware для вызовов Flight Service."""

from collections import deque
import logging
from threading import Lock
from time import monotonic

import grpc


logger = logging.getLogger(__name__)
UNAVAILABLE_CODES = (grpc.StatusCode.UNAVAILABLE, grpc.StatusCode.DEADLINE_EXCEEDED)


class CircuitOpen(grpc.RpcError):
    def code(self):
        return grpc.StatusCode.UNAVAILABLE

    def details(self):
        return "Flight Service circuit breaker is open; try again later"


class CircuitBreaker:
    def __init__(self, threshold, timeout, window):
        self.threshold, self.timeout, self.window = threshold, timeout, window
        self.state = "CLOSED"
        self.failures = deque(maxlen=threshold)
        self.opened_at = 0
        self.generation = 0
        self.lock = Lock()

    def _transition(self, state):
        logger.info("Circuit Breaker: %s -> %s", self.state, state)
        self.state = state
        self.generation += 1

    def _before(self):
        with self.lock:
            if self.state == "OPEN":
                if monotonic() - self.opened_at < self.timeout:
                    raise CircuitOpen()
                self._transition("HALF_OPEN")
            elif self.state == "HALF_OPEN":
                # Пробный вызов уже выполняется в другом потоке.
                raise CircuitOpen()
            return self.generation

    def _after(self, generation, failed):
        with self.lock:
            # Запоздалый ответ из прежнего состояния не меняет новое состояние.
            if generation != self.generation:
                return
            if not failed:
                self.failures.clear()
                if self.state == "HALF_OPEN":
                    self._transition("CLOSED")
                return
            now = monotonic()
            while self.failures and now - self.failures[0] >= self.window:
                self.failures.popleft()
            self.failures.append(now)
            if self.state == "HALF_OPEN" or len(self.failures) >= self.threshold:
                self.opened_at = now
                self._transition("OPEN")

    def call(self, operation, *args):
        generation = self._before()
        try:
            result = operation(*args)
        except grpc.RpcError as error:
            # Бизнес-ошибка подтверждает, что сервис доступен.
            self._after(generation, error.code() in UNAVAILABLE_CODES)
            raise
        except Exception:
            # В частности, не оставляем HALF_OPEN навсегда при неожиданной ошибке.
            self._after(generation, True)
            raise
        self._after(generation, False)
        return result
