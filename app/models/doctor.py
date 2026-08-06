from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin


class Doctor(Base, SoftDeleteMixin):
    __tablename__ = "doctors"

    id = Column(Integer, primary_key=True, index=True)
    user_id = Column(Integer, ForeignKey("users.id"), unique=True, nullable=True)
    first_name = Column(String(100), nullable=False)
    last_name = Column(String(100), nullable=False)
    email = Column(String, unique=True, index=True)
    phone = Column(String(20))
    specialization = Column(String(200), nullable=False)
    license_number = Column(String(100), unique=True, nullable=False)
    years_of_experience = Column(Integer)
    bio = Column(Text)
    certifications = Column(Text)
    languages_spoken = Column(Text)
    ratings = Column(Integer)
    profile_picture_url = Column(String(255))
    department = Column(String(100))
    latitude = Column(Float, nullable=True)
    longitude = Column(Float, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    appointments = relationship("Appointment", back_populates="doctor")
    medical_records = relationship("MedicalRecord", back_populates="doctor")
