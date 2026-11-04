"""Создание рейсов и резерваций с ограничениями целостности."""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "flights",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("flight_number", sa.String(16), nullable=False),
        sa.Column("airline", sa.Text(), nullable=False),
        sa.Column("origin", sa.String(3), nullable=False),
        sa.Column("destination", sa.String(3), nullable=False),
        sa.Column("departure_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("arrival_time", sa.DateTime(timezone=True), nullable=False),
        sa.Column("total_seats", sa.Integer(), nullable=False),
        sa.Column("available_seats", sa.Integer(), nullable=False),
        sa.Column("price_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("total_seats > 0", name="flight_positive_capacity"),
        sa.CheckConstraint("available_seats BETWEEN 0 AND total_seats", name="flight_available_seats"),
        sa.CheckConstraint("price_kopecks > 0", name="flight_positive_price"),
        sa.CheckConstraint("arrival_time > departure_time", name="flight_valid_times"),
        sa.CheckConstraint("origin ~ '^[A-Z]{3}$' AND destination ~ '^[A-Z]{3}$'", name="flight_iata_codes"),
        sa.CheckConstraint("origin <> destination", name="flight_distinct_airports"),
        sa.CheckConstraint("length(trim(flight_number)) > 0 AND length(trim(airline)) > 0", name="flight_nonempty_names"),
        sa.CheckConstraint("status IN ('SCHEDULED', 'DEPARTED', 'CANCELLED', 'COMPLETED')", name="flight_valid_status"),
    )
    # Уникальны номер и календарная дата UTC, а не точный момент вылета.
    op.execute("CREATE UNIQUE INDEX uq_flight_number_date ON flights "
               "(flight_number, ((departure_time AT TIME ZONE 'UTC')::date))")
    op.create_index("ix_flights_route", "flights", ["origin", "destination"])
    op.create_table(
        "seat_reservations",
        sa.Column("booking_id", sa.Uuid(), primary_key=True),
        sa.Column("flight_id", sa.Uuid(), sa.ForeignKey("flights.id"), nullable=False),
        sa.Column("seat_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("seat_count > 0", name="reservation_positive_seats"),
        sa.CheckConstraint("status IN ('ACTIVE', 'RELEASED', 'EXPIRED')", name="reservation_valid_status"),
    )
    op.create_index("ix_reservations_flight_id", "seat_reservations", ["flight_id"])


def downgrade() -> None:
    op.drop_table("seat_reservations")
    op.drop_table("flights")
