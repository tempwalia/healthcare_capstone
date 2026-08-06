from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.sql import func

from app.database.base import Base


class InsurancePlan(Base):
    __tablename__ = "insurance_plans"

    id = Column(Integer, primary_key=True, index=True)
    name = Column(String(100), nullable=False)
    provider = Column(String(100), nullable=False)
    coverage_details = Column(Text, nullable=True)
    created_at = Column(DateTime(timezone=True), server_default=func.now())
    updated_at = Column(DateTime(timezone=True), onupdate=func.now())


class DoctorInsuranceNetwork(Base):
    """Which insurance plans a doctor is in-network for — drives specialist
    recommendation's network-matching (AI Opportunity #2)."""

    __tablename__ = "doctor_insurance_networks"

    id = Column(Integer, primary_key=True, index=True)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    insurance_plan_id = Column(Integer, ForeignKey("insurance_plans.id"), nullable=False)
