from __future__ import annotations

from datetime import datetime, time
from decimal import Decimal

from sqlalchemy import (
    JSON,
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    Numeric,
    String,
    Time,
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
    subscription_setup_draft: Mapped[SubscriptionSetupDraftModel | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    subscriptions: Mapped[list[SubscriptionModel]] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    subscription_edit_draft: Mapped[SubscriptionEditDraftModel | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )
    preferences: Mapped[UserPreferencesModel | None] = relationship(
        back_populates="user",
        cascade="all, delete-orphan",
    )


class UserPreferencesModel(Base):
    __tablename__ = "user_preferences"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    editor_schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    language: Mapped[str] = mapped_column(String(16), nullable=False, default="ru")
    timezone_name: Mapped[str] = mapped_column(
        String(64),
        nullable=False,
        default="Europe/Moscow",
    )
    default_location: Mapped[dict[str, object] | None] = mapped_column(JSON)
    default_radius_meters: Mapped[int | None] = mapped_column(Integer)
    default_source_codes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    notify_low_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_orderable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_price: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completion_mode: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="continue",
    )
    quiet_hours_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    quiet_hours_start: Mapped[time] = mapped_column(
        Time,
        nullable=False,
        default=lambda: time(22, 0),
    )
    quiet_hours_end: Mapped[time] = mapped_column(
        Time,
        nullable=False,
        default=lambda: time(8, 0),
    )
    digest_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    max_points_per_message: Mapped[int] = mapped_column(Integer, nullable=False, default=5)
    editor_status: Mapped[str] = mapped_column(String(32), nullable=False, default="idle")
    editor_location_mode: Mapped[str | None] = mapped_column(String(32))
    editor_location_candidates: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    editor_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    user: Mapped[UserModel] = relationship(back_populates="preferences")


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


class CanonicalProductModel(Base):
    __tablename__ = "canonical_products"
    __table_args__ = (
        UniqueConstraint("critical_signature", name="uq_canonical_products_signature"),
        CheckConstraint("version > 0", name="ck_canonical_products_version_positive"),
        CheckConstraint(
            "package_count IS NULL OR package_count > 0",
            name="ck_canonical_products_package_count_positive",
        ),
        CheckConstraint(
            "kind IN ('medicine', 'other')",
            name="ck_canonical_products_kind",
        ),
        CheckConstraint(
            "quality IN ('partial', 'verified', 'retired')",
            name="ck_canonical_products_quality",
        ),
        CheckConstraint(
            "dosage_value IS NULL OR dosage_value > 0",
            name="ck_canonical_products_dosage_positive",
        ),
        CheckConstraint(
            "concentration_numerator_value IS NULL OR concentration_numerator_value > 0",
            name="ck_canonical_products_concentration_numerator_positive",
        ),
        CheckConstraint(
            "concentration_denominator_value IS NULL OR concentration_denominator_value > 0",
            name="ck_canonical_products_concentration_denominator_positive",
        ),
        CheckConstraint(
            "volume_value IS NULL OR volume_value > 0",
            name="ck_canonical_products_volume_positive",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    quality: Mapped[str] = mapped_column(String(32), nullable=False)
    critical_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    trade_name_raw: Mapped[str] = mapped_column(String(512), nullable=False)
    trade_name_normalized: Mapped[str] = mapped_column(String(512), nullable=False)
    active_ingredient_raw: Mapped[str | None] = mapped_column(String(512))
    active_ingredient_normalized: Mapped[str | None] = mapped_column(String(512))
    manufacturer_raw: Mapped[str | None] = mapped_column(String(512))
    manufacturer_normalized: Mapped[str | None] = mapped_column(String(512))
    form_raw: Mapped[str | None] = mapped_column(String(128))
    form_normalized: Mapped[str | None] = mapped_column(String(128))
    dosage_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    dosage_unit: Mapped[str | None] = mapped_column(String(16))
    dosage_dimension: Mapped[str | None] = mapped_column(String(16))
    concentration_numerator_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    concentration_numerator_unit: Mapped[str | None] = mapped_column(String(16))
    concentration_numerator_dimension: Mapped[str | None] = mapped_column(String(16))
    concentration_denominator_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    concentration_denominator_unit: Mapped[str | None] = mapped_column(String(16))
    concentration_denominator_dimension: Mapped[str | None] = mapped_column(String(16))
    package_count: Mapped[int | None] = mapped_column(Integer)
    volume_value: Mapped[Decimal | None] = mapped_column(Numeric(24, 9))
    volume_unit: Mapped[str | None] = mapped_column(String(16))
    volume_dimension: Mapped[str | None] = mapped_column(String(16))
    route_raw: Mapped[str | None] = mapped_column(String(128))
    route_normalized: Mapped[str | None] = mapped_column(String(128))
    package_variant_raw: Mapped[str | None] = mapped_column(String(256))
    package_variant_normalized: Mapped[str | None] = mapped_column(String(256))
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


class ProductIdentifierModel(Base):
    __tablename__ = "product_identifiers"
    __table_args__ = (
        UniqueConstraint("namespace", "value", name="uq_product_identifiers_namespace_value"),
        Index("ix_product_identifiers_product_status", "product_id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    namespace: Mapped[str] = mapped_column(String(64), nullable=False)
    value: Mapped[str] = mapped_column(String(256), nullable=False)
    issuer: Mapped[str] = mapped_column(String(256), nullable=False)
    trust: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class CanonicalProductVersionModel(Base):
    __tablename__ = "canonical_product_versions"
    __table_args__ = (
        UniqueConstraint("product_id", "version", name="uq_product_versions_product_version"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    identity_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    critical_signature: Mapped[str] = mapped_column(String(64), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class ProductAttributeProvenanceModel(Base):
    __tablename__ = "product_attribute_provenance"
    __table_args__ = (Index("ix_product_provenance_product_field", "product_id", "field_name"),)

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    product_version: Mapped[int] = mapped_column(Integer, nullable=False)
    field_name: Mapped[str] = mapped_column(String(64), nullable=False)
    source_kind: Mapped[str] = mapped_column(String(64), nullable=False)
    source_reference: Mapped[str] = mapped_column(String(256), nullable=False)
    raw_value: Mapped[str | None] = mapped_column(String(1024))
    normalized_value: Mapped[str | None] = mapped_column(String(1024))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    data_version: Mapped[str] = mapped_column(String(128), nullable=False)


class ProductSelectionDraftModel(Base):
    __tablename__ = "product_selection_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
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


class SubscriptionSetupDraftModel(Base):
    __tablename__ = "subscription_setup_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    product_candidate_key: Mapped[str] = mapped_column(String(256), nullable=False)
    product_version: Mapped[str] = mapped_column(String(128), nullable=False)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False)
    product_form: Mapped[str | None] = mapped_column(String(128))
    product_dosage: Mapped[str | None] = mapped_column(String(128))
    product_package: Mapped[str | None] = mapped_column(String(128))
    product_manufacturer: Mapped[str | None] = mapped_column(String(256))
    product_source_host: Mapped[str | None] = mapped_column(String(255))
    canonical_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="SET NULL"),
    )
    canonical_product_version: Mapped[int | None] = mapped_column(Integer)
    location_mode: Mapped[str | None] = mapped_column(String(32))
    location_candidates: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    location: Mapped[dict[str, object] | None] = mapped_column(JSON)
    radius_meters: Mapped[int | None] = mapped_column(Integer)
    available_sources: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    selected_source_codes: Mapped[list[str]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    notify_low_stock: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    notify_orderable: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    include_price: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    completion_mode: Mapped[str | None] = mapped_column(String(32))
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
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

    user: Mapped[UserModel] = relationship(back_populates="subscription_setup_draft")


class SubscriptionModel(Base):
    __tablename__ = "subscriptions"
    __table_args__ = (
        Index("ix_subscriptions_user_status_created", "user_id", "status", "created_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    setup_draft_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    creation_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
    product_candidate_key: Mapped[str] = mapped_column(String(256), nullable=False)
    product_version: Mapped[str] = mapped_column(String(128), nullable=False)
    product_name: Mapped[str] = mapped_column(String(512), nullable=False)
    product_form: Mapped[str | None] = mapped_column(String(128))
    product_dosage: Mapped[str | None] = mapped_column(String(128))
    product_package: Mapped[str | None] = mapped_column(String(128))
    product_manufacturer: Mapped[str | None] = mapped_column(String(256))
    product_source_host: Mapped[str | None] = mapped_column(String(255))
    canonical_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="SET NULL"),
    )
    canonical_product_version: Mapped[int | None] = mapped_column(Integer)
    location_kind: Mapped[str] = mapped_column(String(32), nullable=False)
    location_key: Mapped[str] = mapped_column(String(256), nullable=False)
    location_display_name: Mapped[str] = mapped_column(String(512), nullable=False)
    location_city: Mapped[str | None] = mapped_column(String(256))
    location_address: Mapped[str | None] = mapped_column(String(512))
    location_latitude: Mapped[float | None] = mapped_column(Float)
    location_longitude: Mapped[float | None] = mapped_column(Float)
    radius_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    source_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    notify_low_stock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notify_orderable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    include_price: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completion_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    availability_state: Mapped[str] = mapped_column(String(32), nullable=False)
    state_updated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_successful_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    freshness_expires_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    state_source_name: Mapped[str | None] = mapped_column(String(128))
    has_partial_source_error: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    manual_check_in_progress: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=False,
    )
    next_manual_check_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
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

    user: Mapped[UserModel] = relationship(back_populates="subscriptions")


class SubscriptionEditDraftModel(Base):
    __tablename__ = "subscription_edit_drafts"

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    subscription_id: Mapped[int] = mapped_column(
        ForeignKey("subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    schema_version: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
        default=1,
        server_default="1",
    )
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    base_updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    location_mode: Mapped[str | None] = mapped_column(String(32))
    location_candidates: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    location: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    radius_meters: Mapped[int] = mapped_column(Integer, nullable=False)
    available_sources: Mapped[list[dict[str, object]]] = mapped_column(
        JSON,
        nullable=False,
        default=list,
    )
    selected_source_codes: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    notify_low_stock: Mapped[bool] = mapped_column(Boolean, nullable=False)
    notify_orderable: Mapped[bool] = mapped_column(Boolean, nullable=False)
    include_price: Mapped[bool] = mapped_column(Boolean, nullable=False)
    completion_mode: Mapped[str] = mapped_column(String(32), nullable=False)
    ends_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    idempotency_key: Mapped[str] = mapped_column(String(64), nullable=False, unique=True)
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

    user: Mapped[UserModel] = relationship(back_populates="subscription_edit_draft")


class AuditLogModel(Base):
    __tablename__ = "audit_logs"
    __table_args__ = (
        Index("ix_audit_logs_user_subscription_time", "user_id", "subscription_id", "occurred_at"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    action: Mapped[str] = mapped_column(String(64), nullable=False)
    metadata_json: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class TelegramUpdateReceiptModel(Base):
    __tablename__ = "telegram_update_receipts"

    update_id: Mapped[int] = mapped_column(BigInteger, primary_key=True)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    lease_until: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_error_id: Mapped[str | None] = mapped_column(String(36))
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
