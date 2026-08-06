from sqlalchemy import Boolean, Column, DateTime, ForeignKey, Integer, String
from sqlalchemy.sql import func

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin


class DoctorAvailability(Base, SoftDeleteMixin):
    """Recurring weekly availability window a doctor's bookable slots are generated from."""

    __tablename__ = "doctor_availability"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    weekday = Column(Integer, nullable=False)  # 0=Monday .. 6=Sunday
    start_time = Column(String(5), nullable=False)  # "09:00"
    end_time = Column(String(5), nullable=False)  # "17:00"
    slot_minutes = Column(Integer, default=30)
    created_at = Column(DateTime(timezone=True), server_default=func.now())


class ScheduleSlot(Base, SoftDeleteMixin):
    """A single bookable appointment slot, materialized from DoctorAvailability."""

    __tablename__ = "schedule_slots"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    starts_at = Column(DateTime(timezone=True), nullable=False)
    ends_at = Column(DateTime(timezone=True), nullable=False)
    is_booked = Column(Boolean, default=False)
    appointment_id = Column(Integer, ForeignKey("appointments.id"), nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
