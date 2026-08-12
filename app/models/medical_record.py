from sqlalchemy import Column, DateTime, Float, ForeignKey, Integer, String, Text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database.base import Base
from app.models.mixins import SoftDeleteMixin


class MedicalRecord(Base, SoftDeleteMixin):
    __tablename__ = "medical_records"

    id = Column(Integer, primary_key=True, index=True)
    patient_id = Column(Integer, ForeignKey("patients.id"), nullable=False)
    # Nullable: a patient's own direct document upload (no referral, no
    # treating doctor involved yet) has no doctor to attribute the record
    # to — see app.services.storage.save_medical_record_document and the
    # POST /medical-records/quick-upload route.
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=True)
    visit_date = Column(DateTime(timezone=True), nullable=False)
    diagnosis = Column(String(500))
    symptoms = Column(Text)
    treatment = Column(Text)
    prescription = Column(Text)
    notes = Column(Text)

    blood_pressure_systolic = Column(Integer)
    blood_pressure_diastolic = Column(Integer)
    heart_rate = Column(Integer)
    temperature = Column(Float)
    weight = Column(Float)
    height = Column(Float)

    record_type = Column(String(50))
    attachments = Column(Text)
    version = Column(Integer, default=1)
    access_log = Column(Text)

    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())

    patient = relationship("Patient", back_populates="medical_records")
    doctor = relationship("Doctor", back_populates="medical_records")


class MedicalRecordDocument(Base):
    """A file attached directly to a patient's medical record — the
    general-purpose counterpart to ReferralDocument, which only attaches to
    a referral. Populated via POST /medical-records/{id}/documents or the
    one-call POST /medical-records/quick-upload."""

    __tablename__ = "medical_record_documents"

    id = Column(Integer, primary_key=True, index=True)
    medical_record_id = Column(Integer, ForeignKey("medical_records.id"), nullable=False)
    filename = Column(String(255), nullable=False)
    storage_path = Column(String(500), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
