from __future__ import annotations

from datetime import datetime

from sqlalchemy import (
    BigInteger,
    DateTime,
    ForeignKey,
    Index,
    Integer,
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
    product_selection_draft: Mapped[ProductSelectionDraftModel | None] = relationship(
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


class ProductSelectionDraftModel(Base):
    __tablename__ = "product_selection_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    input_mode: Mapped[str | None] = mapped_column(String(16))
    query_text: Mapped[str | None] = mapped_column(String(4096))
    source_host: Mapped[str | None] = mapped_column(String(255))
    selected_ordinal: Mapped[int | None] = mapped_column(Integer)
    selected_candidate_version: Mapped[str | None] = mapped_column(String(128))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
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

    user: Mapped[UserModel] = relationship(back_populates="product_selection_draft")
    candidates: Mapped[list[ProductSelectionCandidateModel]] = relationship(
        back_populates="draft",
        cascade="all, delete-orphan",
        order_by="ProductSelectionCandidateModel.ordinal",
    )


class ProductSelectionCandidateModel(Base):
    __tablename__ = "product_selection_candidates"
    __table_args__ = (
        UniqueConstraint(
            "draft_id",
            "ordinal",
            name="uq_product_selection_candidates_draft_ordinal",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    draft_id: Mapped[int] = mapped_column(
        ForeignKey("product_selection_drafts.id", ondelete="CASCADE"),
        nullable=False,
    )
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    candidate_key: Mapped[str] = mapped_column(String(256), nullable=False)
    version: Mapped[str] = mapped_column(String(128), nullable=False)
    name: Mapped[str] = mapped_column(String(512), nullable=False)
    form: Mapped[str | None] = mapped_column(String(128))
    dosage: Mapped[str | None] = mapped_column(String(128))
    package: Mapped[str | None] = mapped_column(String(128))
    manufacturer: Mapped[str | None] = mapped_column(String(256))
    source_name: Mapped[str | None] = mapped_column(String(128))
    source_host: Mapped[str | None] = mapped_column(String(255))
    confidence: Mapped[str] = mapped_column(String(32), nullable=False)

    draft: Mapped[ProductSelectionDraftModel] = relationship(back_populates="candidates")
