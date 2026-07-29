from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship


class Base(DeclarativeBase):
    pass


class UserModel(Base):
    __tablename__ = "users"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    telegram_user_id: Mapped[int] = mapped_column(BigInteger, nullable=False, unique=True)
    telegram_chat_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    language_code: Mapped[str | None] = mapped_column(String(16))
    onboarding_status: Mapped[str] = mapped_column(String(32), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
        server_default=func.now(),
        onupdate=func.now(),
    )

    consent_decisions: Mapped[list[ConsentDecisionModel]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class ConsentDecisionModel(Base):
    __tablename__ = "consent_decisions"
    __table_args__ = (
        UniqueConstraint(
            "user_id",
            "terms_version",
            "privacy_version",
            "decision",
            name="uq_consent_decisions_user_versions_decision",
        ),
        Index("ix_consent_decisions_user_occurred_at", "user_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    terms_version: Mapped[str] = mapped_column(String(64), nullable=False)
    privacy_version: Mapped[str] = mapped_column(String(64), nullable=False)
    decision: Mapped[str] = mapped_column(String(16), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    method: Mapped[str] = mapped_column(String(64), nullable=False)

    user: Mapped[UserModel] = relationship(back_populates="consent_decisions")
