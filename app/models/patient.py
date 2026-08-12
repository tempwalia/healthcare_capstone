import enum

from sqlalchemy import Column, Date, DateTime, Enum, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin


class GenderEnum(enum.Enum):
    MALE = "male"
    FEMALE = "female"
    OTHER = "other"


class Patient(Base, SoftDeleteMixin):
    __tablename__ = "patients"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String, unique=True, index=True)
    phone = Column(String(20))
    date_of_birth = Column(Date, nullable=False)
    gender = Column(Enum(GenderEnum), nullable=False)
    address = Column(Text)
    # Simple city/region text match for doctor-proximity ranking — see
    # app.services.doctor_recommendation.recommend_platform_doctors. Not
    # geocoded; deliberately just a free-text label to compare against
    # Doctor.city.
    city = Column(String(100), nullable=True)
    emergency_contact_name = Column(String(200))
    emergency_contact_phone = Column(String(20))
    insurance_provider = Column(String(200))
    insurance_policy_number = Column(String(100))
    allergies = Column(Text)
    blood_type = Column(String(5))
    preferred_language = Column(String(50))
    lifestyle = Column(Text)
    family_history = Column(Text)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    appointments = relationship("Appointment", back_populates="patient")
    medical_records = relationship("MedicalRecord", back_populates="patient")
