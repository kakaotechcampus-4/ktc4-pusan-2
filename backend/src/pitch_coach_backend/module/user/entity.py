from sqlalchemy import String
from sqlalchemy.orm import Mapped, mapped_column

from pitch_coach_backend.core.database import Base, TimestampMixin, UUIDPrimaryKeyMixin

NAME_MAX_LENGTH = 100


class User(UUIDPrimaryKeyMixin, TimestampMixin, Base):
    __tablename__ = "users"

    email: Mapped[str] = mapped_column(String(255), unique=True, nullable=False)
    name: Mapped[str] = mapped_column(String(NAME_MAX_LENGTH), nullable=False)
