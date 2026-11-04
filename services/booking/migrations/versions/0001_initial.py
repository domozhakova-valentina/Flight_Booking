"""Создание таблицы бронирований."""

from alembic import op
import sqlalchemy as sa


revision = "0001"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "bookings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("user_id", sa.Uuid(), nullable=False),
        sa.Column("flight_id", sa.Uuid(), nullable=False),
        sa.Column("passenger_name", sa.Text(), nullable=False),
        sa.Column("passenger_email", sa.Text(), nullable=False),
        sa.Column("seat_count", sa.Integer(), nullable=False),
        sa.Column("total_price_kopecks", sa.BigInteger(), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.CheckConstraint("seat_count > 0", name="booking_positive_seats"),
        sa.CheckConstraint("total_price_kopecks > 0", name="booking_positive_price"),
        sa.CheckConstraint("status IN ('CONFIRMED', 'CANCELLED')", name="booking_valid_status"),
        sa.CheckConstraint("length(trim(passenger_name)) > 0", name="booking_nonempty_name"),
        sa.CheckConstraint("length(trim(passenger_email)) > 0", name="booking_nonempty_email"),
    )
    op.create_index("ix_bookings_user_id", "bookings", ["user_id"])


def downgrade() -> None:
    op.drop_table("bookings")
