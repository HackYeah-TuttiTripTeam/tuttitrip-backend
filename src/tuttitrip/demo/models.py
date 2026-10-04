"""Demo ORM model: which accounts already got their sample trip."""

from datetime import datetime

from sqlalchemy import String, func
from sqlalchemy.orm import Mapped, mapped_column

from tuttitrip.shared.db.base import Base


class SampleTripGrant(Base):
    """Marks an account that got the sample trip, once for its whole life.

    ``user_sub`` is an Auth0 id like in ``trip_members`` (no users table, no
    FK). The mark outlives the trip, so a sample the user deleted never comes
    back; account erasure removes it.
    """

    __tablename__ = "sample_trip_grants"

    user_sub: Mapped[str] = mapped_column(String(255), primary_key=True)
    created_at: Mapped[datetime] = mapped_column(server_default=func.now())
