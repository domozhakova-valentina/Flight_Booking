"""Check API Key on all RPCs, without creating database records.

Run inside Booking Service: python - < tests/test_grpc_auth.py
"""

import unittest
from uuid import uuid4

import grpc

from app import flight_client
from app.config import GRPC_API_KEY
from app.generated import flight_pb2 as p


class ApiKeyTests(unittest.TestCase):
    def test_all_methods(self):
        flight_id, booking_id = uuid4(), uuid4()
        cases = [
            ("SearchFlights", p.SearchFlightsRequest(origin="SVO", destination="LED"),
             lambda: flight_client.search_flights("SVO", "LED"), None),
            ("GetFlight", p.GetFlightRequest(flight_id=str(flight_id)),
             lambda: flight_client.get_flight(flight_id), grpc.StatusCode.NOT_FOUND),
            ("ReserveSeats", p.ReserveSeatsRequest(flight_id=str(flight_id),
             booking_id=str(booking_id), seat_count=0),
             lambda: flight_client.reserve_seats(flight_id, 0, booking_id), grpc.StatusCode.INVALID_ARGUMENT),
            ("ReleaseReservation", p.ReleaseReservationRequest(booking_id=str(booking_id)),
             lambda: flight_client.release_reservation(booking_id), grpc.StatusCode.NOT_FOUND),
            ("UpdateFlight", p.UpdateFlightRequest(flight_id=str(flight_id)),
             lambda: flight_client.stub.UpdateFlight(p.UpdateFlightRequest(flight_id=str(flight_id)),
                 timeout=5, metadata=flight_client.metadata), grpc.StatusCode.INVALID_ARGUMENT),
        ]
        for name, request, client_call, expected in cases:
            for label, metadata in [
                ("missing", None),
                ("invalid", (("x-api-key", GRPC_API_KEY + "-wrong"),)),
            ]:
                with self.subTest(method=name, key=label):
                    with self.assertRaises(grpc.RpcError) as caught:
                        getattr(flight_client.stub, name)(request, timeout=5, metadata=metadata)
                    self.assertEqual(caught.exception.code(), grpc.StatusCode.UNAUTHENTICATED)
            with self.subTest(method=name, key="valid"):
                if expected is None:
                    client_call()
                else:
                    with self.assertRaises(grpc.RpcError) as caught:
                        client_call()
                    self.assertEqual(caught.exception.code(), expected)


if __name__ == "__main__":
    unittest.main(verbosity=2)
