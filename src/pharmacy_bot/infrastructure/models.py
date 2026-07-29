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


class SourceProductModel(Base):
    __tablename__ = "source_products"
    __table_args__ = (
        UniqueConstraint(
            "source_code",
            "external_id",
            name="uq_source_products_source_external",
        ),
        CheckConstraint("version > 0", name="ck_source_products_version_positive"),
        CheckConstraint(
            "status IN ('active', 'discontinued', 'unavailable')",
            name="ck_source_products_status",
        ),
        CheckConstraint(
            "monitoring_eligibility IN "
            "('pending_revalidation', 'eligible', 'quarantined', 'awaiting_fresh_check')",
            name="ck_source_products_monitoring_eligibility",
        ),
        Index("ix_source_products_search", "id", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(256), nullable=False)
    canonical_url: Mapped[str] = mapped_column(String(2048), nullable=False)
    raw_name: Mapped[str] = mapped_column(String(512), nullable=False)
    parsed_attributes: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    search_document: Mapped[str] = mapped_column(String(2048), nullable=False)
    canonical_product_id: Mapped[int | None] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="SET NULL"),
    )
    canonical_product_version: Mapped[int | None] = mapped_column(Integer)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    monitoring_eligibility: Mapped[str] = mapped_column(
        String(32),
        nullable=False,
        default="pending_revalidation",
    )
    quarantine_reason: Mapped[str | None] = mapped_column(String(128))
    quarantined_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_revalidated_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    last_revalidated_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    fresh_check_required: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceProductVersionModel(Base):
    __tablename__ = "source_product_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_product_id",
            "version",
            name="uq_source_product_versions_product_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_product_id: Mapped[int] = mapped_column(
        ForeignKey("source_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    semantic_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    changed_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class MappingDecisionModel(Base):
    __tablename__ = "mapping_decisions"
    __table_args__ = (
        UniqueConstraint(
            "actor_type",
            "actor_internal_id",
            "idempotency_key",
            name="uq_mapping_decisions_actor_idempotency",
        ),
        CheckConstraint("version > 0", name="ck_mapping_decisions_version_positive"),
        CheckConstraint(
            "actor_type IN ('user', 'operator')",
            name="ck_mapping_decisions_actor_type",
        ),
        CheckConstraint(
            "scope IN ('user', 'source', 'global')",
            name="ck_mapping_decisions_scope",
        ),
        CheckConstraint(
            "status IN ('active', 'revoked')",
            name="ck_mapping_decisions_status",
        ),
        CheckConstraint(
            "(scope = 'user' AND scope_user_id IS NOT NULL) "
            "OR (scope <> 'user' AND scope_user_id IS NULL)",
            name="ck_mapping_decisions_scope_user",
        ),
        Index(
            "ix_mapping_decisions_active_lookup",
            "source_product_id",
            "canonical_product_id",
            "status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_product_id: Mapped[int] = mapped_column(
        ForeignKey("source_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_product_id: Mapped[int] = mapped_column(
        ForeignKey("canonical_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_product_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_type: Mapped[str] = mapped_column(String(16), nullable=False)
    actor_internal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    scope: Mapped[str] = mapped_column(String(16), nullable=False)
    scope_user_id: Mapped[int | None] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
    )
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(128), nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    revoked_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class SourceProductRevalidationModel(Base):
    __tablename__ = "source_product_revalidations"
    __table_args__ = (
        UniqueConstraint(
            "source_product_id",
            "source_version",
            "algorithm_version",
            name="uq_source_revalidations_product_version_algorithm",
        ),
        Index(
            "ix_source_revalidations_product_created",
            "source_product_id",
            "created_at",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_product_id: Mapped[int] = mapped_column(
        ForeignKey("source_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    previous_version: Mapped[int] = mapped_column(Integer, nullable=False)
    source_version: Mapped[int] = mapped_column(Integer, nullable=False)
    drift_class: Mapped[str] = mapped_column(String(32), nullable=False)
    match_level: Mapped[str] = mapped_column(String(32), nullable=False)
    match_confirmed: Mapped[bool] = mapped_column(Boolean, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    match_algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    action: Mapped[str] = mapped_column(String(32), nullable=False)
    safe_evidence: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    actor_type: Mapped[str | None] = mapped_column(String(32))
    actor_internal_id: Mapped[int | None] = mapped_column(BigInteger)
    reason_code: Mapped[str | None] = mapped_column(String(128))
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LocationScopeModel(Base):
    __tablename__ = "location_scopes"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_location_scopes_fingerprint"),
        CheckConstraint("version > 0", name="ck_location_scopes_version_positive"),
        CheckConstraint(
            "kind IN ('country', 'region', 'city', 'district', 'radius', 'address', "
            "'pharmacy_list', 'online_region')",
            name="ck_location_scopes_kind",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_location_scopes_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_location_scopes_longitude",
        ),
        CheckConstraint(
            "radius_meters IS NULL OR radius_meters > 0",
            name="ck_location_scopes_radius_positive",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    country_key: Mapped[str | None] = mapped_column(String(128))
    region_key: Mapped[str | None] = mapped_column(String(128))
    city_key: Mapped[str | None] = mapped_column(String(128))
    district_key: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    radius_meters: Mapped[int | None] = mapped_column(Integer)
    address_key: Mapped[str | None] = mapped_column(String(128))
    pharmacy_ids: Mapped[list[int]] = mapped_column(JSON, nullable=False, default=list)
    online_region_key: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class LocationScopeVersionModel(Base):
    __tablename__ = "location_scope_versions"
    __table_args__ = (
        UniqueConstraint(
            "location_scope_id",
            "version",
            name="uq_location_scope_versions_scope_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    location_scope_id: Mapped[int] = mapped_column(
        ForeignKey("location_scopes.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class GeocodingSessionModel(Base):
    __tablename__ = "geocoding_sessions"
    __table_args__ = (
        UniqueConstraint("user_id", "generation", name="uq_geocoding_sessions_user_generation"),
        CheckConstraint(
            "status IN ('exact', 'ambiguous', 'insufficient', 'confirmed')",
            name="ck_geocoding_sessions_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    user_id: Mapped[int] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    generation: Mapped[int] = mapped_column(Integer, nullable=False)
    query_hash: Mapped[str] = mapped_column(String(64), nullable=False)
    locale: Mapped[str] = mapped_column(String(16), nullable=False)
    region_hint_hash: Mapped[str | None] = mapped_column(String(64))
    provider_code: Mapped[str] = mapped_column(String(64), nullable=False)
    provider_data_version: Mapped[str] = mapped_column(String(64), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    candidates: Mapped[list[dict[str, object]]] = mapped_column(JSON, nullable=False)
    selected_candidate_id: Mapped[str | None] = mapped_column(String(24))
    expires_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PharmacyModel(Base):
    __tablename__ = "pharmacies"
    __table_args__ = (
        UniqueConstraint("fingerprint", name="uq_pharmacies_fingerprint"),
        CheckConstraint("version > 0", name="ck_pharmacies_version_positive"),
        CheckConstraint(
            "kind IN ('pharmacy', 'pickup_point')",
            name="ck_pharmacies_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'temporarily_closed', 'retired')",
            name="ck_pharmacies_status",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_pharmacies_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_pharmacies_longitude",
        ),
        Index("ix_pharmacies_coordinates_status", "latitude", "longitude", "status"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_address: Mapped[str] = mapped_column(String(512), nullable=False)
    network_key: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    trusted_identifier: Mapped[str | None] = mapped_column(String(128))
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PharmacyVersionModel(Base):
    __tablename__ = "pharmacy_versions"
    __table_args__ = (
        UniqueConstraint(
            "pharmacy_id",
            "version",
            name="uq_pharmacy_versions_pharmacy_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    pharmacy_id: Mapped[int] = mapped_column(
        ForeignKey("pharmacies.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePharmacyModel(Base):
    __tablename__ = "source_pharmacies"
    __table_args__ = (
        UniqueConstraint(
            "source_code",
            "external_id",
            name="uq_source_pharmacies_source_external",
        ),
        CheckConstraint("version > 0", name="ck_source_pharmacies_version_positive"),
        CheckConstraint(
            "mapping_version >= 0",
            name="ck_source_pharmacies_mapping_version_nonnegative",
        ),
        CheckConstraint(
            "kind IN ('pharmacy', 'pickup_point')",
            name="ck_source_pharmacies_kind",
        ),
        CheckConstraint(
            "status IN ('active', 'temporarily_closed', 'retired')",
            name="ck_source_pharmacies_status",
        ),
        CheckConstraint(
            "latitude IS NULL OR (latitude >= -90 AND latitude <= 90)",
            name="ck_source_pharmacies_latitude",
        ),
        CheckConstraint(
            "longitude IS NULL OR (longitude >= -180 AND longitude <= 180)",
            name="ck_source_pharmacies_longitude",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    external_id: Mapped[str] = mapped_column(String(128), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    kind: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    normalized_address: Mapped[str] = mapped_column(String(512), nullable=False)
    network_key: Mapped[str | None] = mapped_column(String(128))
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    trusted_identifier: Mapped[str | None] = mapped_column(String(128))
    canonical_pharmacy_id: Mapped[int | None] = mapped_column(
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
    )
    mapping_level: Mapped[str | None] = mapped_column(String(32))
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourcePharmacyVersionModel(Base):
    __tablename__ = "source_pharmacy_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_pharmacy_id",
            "version",
            name="uq_source_pharmacy_versions_source_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_pharmacy_id: Mapped[int] = mapped_column(
        ForeignKey("source_pharmacies.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    changed_fields: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    observed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PharmacyMappingDecisionModel(Base):
    __tablename__ = "pharmacy_mapping_decisions"
    __table_args__ = (
        UniqueConstraint(
            "actor_internal_id",
            "idempotency_key",
            name="uq_pharmacy_mapping_actor_idempotency",
        ),
        Index(
            "ix_pharmacy_mapping_source_created",
            "source_pharmacy_id",
            "created_at",
        ),
        CheckConstraint(
            "action IN ('confirm', 'revoke')",
            name="ck_pharmacy_mapping_decisions_action",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_pharmacy_id: Mapped[int] = mapped_column(
        ForeignKey("source_pharmacies.id", ondelete="CASCADE"),
        nullable=False,
    )
    canonical_pharmacy_id: Mapped[int | None] = mapped_column(
        ForeignKey("pharmacies.id", ondelete="SET NULL"),
    )
    action: Mapped[str] = mapped_column(String(16), nullable=False)
    match_level: Mapped[str | None] = mapped_column(String(32))
    reasons: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    algorithm_version: Mapped[str] = mapped_column(String(64), nullable=False)
    mapping_version: Mapped[int] = mapped_column(Integer, nullable=False)
    actor_internal_id: Mapped[int] = mapped_column(BigInteger, nullable=False)
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class FulfillmentRecordModel(Base):
    __tablename__ = "fulfillment_records"
    __table_args__ = (
        UniqueConstraint(
            "source_product_id",
            "fulfillment_type",
            "reference_key",
            name="uq_fulfillment_source_type_reference",
        ),
        CheckConstraint("version > 0", name="ck_fulfillment_records_version_positive"),
        CheckConstraint(
            "fulfillment_type IN ('physical_stock', 'pickup', 'delivery', 'online_unknown')",
            name="ck_fulfillment_records_type",
        ),
        CheckConstraint(
            "(fulfillment_type IN ('physical_stock', 'pickup') "
            "AND pharmacy_id IS NOT NULL AND latitude IS NOT NULL AND longitude IS NOT NULL "
            "AND delivery_region_key IS NULL AND delivery_city_key IS NULL) "
            "OR (fulfillment_type = 'delivery' AND pharmacy_id IS NULL "
            "AND latitude IS NULL AND longitude IS NULL "
            "AND (delivery_region_key IS NOT NULL OR delivery_city_key IS NOT NULL)) "
            "OR (fulfillment_type = 'online_unknown' AND pharmacy_id IS NULL "
            "AND latitude IS NULL AND longitude IS NULL "
            "AND delivery_region_key IS NULL AND delivery_city_key IS NULL)",
            name="ck_fulfillment_records_references",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_product_id: Mapped[int] = mapped_column(
        ForeignKey("source_products.id", ondelete="CASCADE"),
        nullable=False,
    )
    fulfillment_type: Mapped[str] = mapped_column(String(32), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    pharmacy_id: Mapped[int | None] = mapped_column(
        ForeignKey("pharmacies.id", ondelete="RESTRICT"),
    )
    latitude: Mapped[Decimal | None] = mapped_column(Numeric(9, 6))
    longitude: Mapped[Decimal | None] = mapped_column(Numeric(10, 6))
    delivery_region_key: Mapped[str | None] = mapped_column(String(128))
    delivery_city_key: Mapped[str | None] = mapped_column(String(128))
    reference_key: Mapped[str] = mapped_column(String(320), nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    first_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceModel(Base):
    __tablename__ = "sources"
    __table_args__ = (
        UniqueConstraint("code", name="uq_sources_code"),
        CheckConstraint("version > 0", name="ck_sources_version_positive"),
        CheckConstraint(
            "source_type IN "
            "('partner_api', 'public_api', 'webhook', 'export', 'public_page', 'manual')",
            name="ck_sources_type",
        ),
        CheckConstraint(
            "status IN ('active', 'disabled', 'degraded')",
            name="ck_sources_status",
        ),
        CheckConstraint(
            "legal_status IN ('allowed', 'review_required', 'blocked')",
            name="ck_sources_legal_status",
        ),
        CheckConstraint(
            "requests_per_window > 0 AND window_seconds > 0 AND max_concurrency > 0 "
            "AND freshness_seconds > 0 AND cache_ttl_seconds >= 0 "
            "AND cache_ttl_seconds <= freshness_seconds",
            name="ck_sources_limits_positive",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    code: Mapped[str] = mapped_column(String(64), nullable=False)
    name: Mapped[str] = mapped_column(String(256), nullable=False)
    source_type: Mapped[str] = mapped_column(String(32), nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    legal_status: Mapped[str] = mapped_column(String(32), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capability_version: Mapped[str] = mapped_column(String(64), nullable=False)
    capabilities: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    base_urls: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    redirect_hosts: Mapped[list[str]] = mapped_column(JSON, nullable=False)
    requests_per_window: Mapped[int] = mapped_column(Integer, nullable=False)
    window_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    max_concurrency: Mapped[int] = mapped_column(Integer, nullable=False)
    freshness_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    cache_ttl_seconds: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False, default=1)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceVersionModel(Base):
    __tablename__ = "source_versions"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "version",
            name="uq_source_versions_source_version",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_snapshot: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    actor_internal_id: Mapped[int | None] = mapped_column(BigInteger)
    reason: Mapped[str] = mapped_column(String(128), nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class AdapterIngestionReceiptModel(Base):
    __tablename__ = "adapter_ingestion_receipts"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "idempotency_key",
            name="uq_adapter_receipts_source_idempotency",
        ),
        CheckConstraint(
            "operation IN ('health', 'search_products', 'get_product', "
            "'list_pharmacies', 'check_availability')",
            name="ck_adapter_receipts_operation",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    idempotency_key: Mapped[str] = mapped_column(String(128), nullable=False)
    request_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    result_fingerprint: Mapped[str] = mapped_column(String(64), nullable=False)
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    causation_id: Mapped[str | None] = mapped_column(String(36))
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    source_code: Mapped[str] = mapped_column(String(64), nullable=False)
    adapter_version: Mapped[str] = mapped_column(String(64), nullable=False)
    contract_version: Mapped[str] = mapped_column(String(64), nullable=False)
    schema_version: Mapped[str] = mapped_column(String(64), nullable=False)
    safe_result: Mapped[dict[str, object]] = mapped_column(JSON, nullable=False)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class WebhookReceiptModel(Base):
    __tablename__ = "webhook_receipts"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "delivery_key",
            name="uq_webhook_receipts_source_delivery",
        ),
        CheckConstraint(
            "status IN ('processing', 'accepted', 'quarantined')",
            name="ck_webhook_receipts_status",
        ),
        CheckConstraint("body_bytes >= 0", name="ck_webhook_receipts_body_bytes"),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="RESTRICT"),
        nullable=False,
    )
    delivery_key: Mapped[str] = mapped_column(String(128), nullable=False)
    body_digest: Mapped[str] = mapped_column(String(64), nullable=False)
    event_timestamp: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    body_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(32), nullable=False)
    business_fingerprint: Mapped[str | None] = mapped_column(String(64))
    quarantine_reason: Mapped[str | None] = mapped_column(String(64))
    received_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))


class IntegrationRequestModel(Base):
    __tablename__ = "integration_requests"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "correlation_id",
            name="uq_integration_requests_source_correlation",
        ),
        CheckConstraint(
            "outcome IN ('success', 'client_failure', 'upstream_failure', "
            "'network_failure', 'contract_failure', 'policy_rejection')",
            name="ck_integration_requests_outcome",
        ),
        CheckConstraint(
            "duration_ms >= 0 AND attempts > 0 AND response_bytes >= 0",
            name="ck_integration_requests_metrics",
        ),
        CheckConstraint(
            "cache_status IS NULL OR cache_status IN ('fresh', 'stale', 'miss')",
            name="ck_integration_requests_cache_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    correlation_id: Mapped[str] = mapped_column(String(36), nullable=False)
    operation: Mapped[str] = mapped_column(String(32), nullable=False)
    outcome: Mapped[str] = mapped_column(String(32), nullable=False)
    duration_ms: Mapped[int] = mapped_column(Integer, nullable=False)
    attempts: Mapped[int] = mapped_column(Integer, nullable=False)
    response_bytes: Mapped[int] = mapped_column(Integer, nullable=False)
    http_status: Mapped[int | None] = mapped_column(Integer)
    cache_status: Mapped[str | None] = mapped_column(String(16))
    failure_code: Mapped[str | None] = mapped_column(String(64))
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceHealthModel(Base):
    __tablename__ = "source_health"
    __table_args__ = (
        CheckConstraint(
            "status IN ('healthy', 'degraded')",
            name="ck_source_health_status",
        ),
        CheckConstraint(
            "consecutive_failures >= 0 AND consecutive_successes >= 0 AND version > 0",
            name="ck_source_health_counters",
        ),
    )

    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        primary_key=True,
    )
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    consecutive_failures: Mapped[int] = mapped_column(Integer, nullable=False)
    consecutive_successes: Mapped[int] = mapped_column(Integer, nullable=False)
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    changed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class SourceHealthEventModel(Base):
    __tablename__ = "source_health_events"
    __table_args__ = (
        UniqueConstraint(
            "source_id",
            "version",
            name="uq_source_health_events_source_version",
        ),
        CheckConstraint(
            "status IN ('healthy', 'degraded')",
            name="ck_source_health_events_status",
        ),
    )

    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    source_id: Mapped[int] = mapped_column(
        ForeignKey("sources.id", ondelete="CASCADE"),
        nullable=False,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(16), nullable=False)
    reason_code: Mapped[str] = mapped_column(String(64), nullable=False)
    occurred_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


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
    location_scope_id: Mapped[int | None] = mapped_column(
        ForeignKey("location_scopes.id", ondelete="SET NULL"),
    )
    location_scope_version: Mapped[int | None] = mapped_column(Integer)
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
    location_scope_id: Mapped[int | None] = mapped_column(
        ForeignKey("location_scopes.id", ondelete="SET NULL"),
    )
    location_scope_version: Mapped[int | None] = mapped_column(Integer)
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
