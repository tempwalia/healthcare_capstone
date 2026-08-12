from sqlalchemy import Column, DateTime, ForeignKey, Integer, String, UniqueConstraint
from sqlalchemy.sql import func

from app.database.base import Base


class ProviderDirectoryLink(Base):
    """Maps a mock provider directory's synthetic doctor_id (e.g. 88, 91 —
    see mock_systems/provider_directory_mock) onto a real platform Doctor
    row, once a care coordinator vouches for the match at approval time.
    Deliberately not a coincidental `doctors.id == external_doctor_id`
    check: the mock ids are small, unnamespaced ints with no structural
    guarantee against colliding with real, autoincrementing doctors.id as
    more real doctors get added — this table is the honest, explicit
    mapping that check would silently get wrong. Entirely optional: a
    referral resumed without a mapping behaves exactly as before this
    feature existed."""

    __tablename__ = "provider_directory_links"

    id = Column(Integer, primary_key=True, index=True)
    source_system = Column(String(60), nullable=False, default="provider_directory_mock")
    external_doctor_id = Column(Integer, nullable=False)
    doctor_id = Column(Integer, ForeignKey("doctors.id"), nullable=False)
    created_by_user_id = Column(Integer, ForeignKey("users.id"), nullable=False)
    created_at = Column(DateTime(timezone=True), server_default=func.now())

    __table_args__ = (
        UniqueConstraint("source_system", "external_doctor_id", name="uq_provider_directory_link_source_external"),
    )
