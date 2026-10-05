"""Full schema for all four phases. Phase 1 fills: offices, catalogs, cases, documents, pages,
exhibits, case_cards, case_requirements, case_events, vault.pii_tokens, audit_log, pipeline_steps.
Phase 2 fills: users, rules layer, asr_rates, case_embeddings, review_assignments, extraction_corrections,
eval_runs (extraction_accuracy / agreement). Phase 3 fills: fee_calculations, case_analyses, eval_runs (loo_tuning /
fee_golden). Phase 4 fills clause_library, drafts, eval_labels, eval_case_results and eval_runs
(holdout_selection / backtest). The GR library (Part 2) fills library_documents, library_pages, library_provisions and
eval_runs (library_search)."""
from __future__ import annotations

import datetime as dt
import uuid
from decimal import Decimal
from typing import Any

from pgvector.sqlalchemy import Vector
from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    LargeBinary,
    Numeric,
    PrimaryKeyConstraint,
    String,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

EMBEDDING_DIM = 1536

VERIFICATION = "status IN ('verified','to_verify')"


class Base(DeclarativeBase):
    type_annotation_map = {dict[str, Any]: JSONB, list[Any]: JSONB}


def _pk() -> Mapped[uuid.UUID]:
    return mapped_column(UUID(as_uuid=True), primary_key=True, server_default=text("gen_random_uuid()"), default=uuid.uuid4)


def _created() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), nullable=False)


def _updated() -> Mapped[dt.datetime]:
    return mapped_column(DateTime(timezone=True), server_default=func.now(), onupdate=func.now(), nullable=False)


# ---------------------------------------------------------------------------------------------
# Organisation & users
# ---------------------------------------------------------------------------------------------
class Office(Base):
    __tablename__ = "offices"
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(32), unique=True)
    name: Mapped[str] = mapped_column(Text)
    name_mr: Mapped[str | None] = mapped_column(Text)
    division: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str | None] = mapped_column(Text)
    taluka: Mapped[str | None] = mapped_column(Text)
    address: Mapped[str | None] = mapped_column(Text)
    is_pilot: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_offices_status"),)


class User(Base):
    __tablename__ = "users"
    id: Mapped[uuid.UUID] = _pk()
    email: Mapped[str] = mapped_column(String(320), unique=True)
    display_name: Mapped[str] = mapped_column(Text)
    role: Mapped[str] = mapped_column(String(16))
    office_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("offices.id"))
    password_hash: Mapped[str | None] = mapped_column(Text)
    is_active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    last_login_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (CheckConstraint("role IN ('reviewer','officer','admin')", name="ck_users_role"),)


# ---------------------------------------------------------------------------------------------
# Catalogs
# ---------------------------------------------------------------------------------------------
class ExhibitType(Base):
    __tablename__ = "exhibit_types"
    code: Mapped[str] = mapped_column(String(48), primary_key=True)
    name_en: Mapped[str] = mapped_column(Text)
    name_mr: Mapped[str] = mapped_column(Text)
    default_stage: Mapped[str] = mapped_column(String(24))
    party: Mapped[str] = mapped_column(String(16))  # applicant | office
    keywords: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    negative_keywords: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    extraction_schema: Mapped[str | None] = mapped_column(String(64))
    priority: Mapped[int] = mapped_column(Integer, server_default="0")
    description: Mapped[str | None] = mapped_column(Text)
    catalog_version: Mapped[int] = mapped_column(Integer, server_default="0")
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


class RequirementCatalog(Base):
    __tablename__ = "requirement_catalog"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_version: Mapped[int] = mapped_column(Integer, server_default="0")
    name_en: Mapped[str] = mapped_column(Text)
    name_mr: Mapped[str] = mapped_column(Text)
    category: Mapped[str] = mapped_column(String(32))
    satisfied_by: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    query_keywords: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    applies_when: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    description: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    intake_slot: Mapped[str | None] = mapped_column(String(32))  # rci.intake.slots key the requirement is filed under
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # set for codes created in review
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_requirement_catalog_status"),)


class EventCatalog(Base):
    __tablename__ = "event_catalog"
    code: Mapped[str] = mapped_column(String(64), primary_key=True)
    catalog_version: Mapped[int] = mapped_column(Integer, server_default="0")
    name_en: Mapped[str] = mapped_column(Text)
    name_mr: Mapped[str] = mapped_column(Text)
    actor: Mapped[str] = mapped_column(String(32))
    typical_order: Mapped[int] = mapped_column(Integer)
    source_exhibit_types: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    date_field: Mapped[str | None] = mapped_column(String(64))
    description: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))


# ---------------------------------------------------------------------------------------------
# Cases, documents, pages, exhibits
# ---------------------------------------------------------------------------------------------
class Case(Base):
    __tablename__ = "cases"
    id: Mapped[uuid.UUID] = _pk()
    office_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("offices.id"))
    case_ref: Mapped[str] = mapped_column(String(128))
    title: Mapped[str | None] = mapped_column(Text)
    case_type: Mapped[str] = mapped_column(String(48), server_default="CHS_CLASS2_TO_CLASS1")
    data_class: Mapped[str] = mapped_column(String(16))  # real | synthetic
    role: Mapped[str] = mapped_column(String(16), server_default="unassigned")
    role_locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(24), server_default="created")
    society_name: Mapped[str | None] = mapped_column(Text)
    filed_on: Mapped[dt.date | None] = mapped_column(Date)
    decided_on: Mapped[dt.date | None] = mapped_column(Date)
    rule_set_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sets.id"))
    last_error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    documents: Mapped[list["Document"]] = relationship(back_populates="case", order_by="Document.seq")
    __table_args__ = (
        UniqueConstraint("office_id", "case_ref", name="uq_cases_office_ref"),
        CheckConstraint("data_class IN ('real','synthetic')", name="ck_cases_data_class"),
        CheckConstraint("role IN ('unassigned','precedent','holdout','incoming')", name="ck_cases_role"),
    )


class Document(Base):
    __tablename__ = "documents"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    sha256: Mapped[str] = mapped_column(String(64))
    filename: Mapped[str] = mapped_column(Text)
    mime_type: Mapped[str] = mapped_column(String(128))
    size_bytes: Mapped[int] = mapped_column(BigInteger)
    blob_key: Mapped[str] = mapped_column(Text)
    parent_document_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("documents.id"))
    slot: Mapped[str | None] = mapped_column(String(32))  # intake slot the file was uploaded for (rci.intake.slots)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(24), server_default="uploaded")
    error: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created()

    case: Mapped[Case] = relationship(back_populates="documents")
    pages: Mapped[list["Page"]] = relationship(back_populates="document", order_by="Page.page_no")
    __table_args__ = (UniqueConstraint("case_id", "sha256", name="uq_documents_case_sha"),)


class Page(Base):
    __tablename__ = "pages"
    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("documents.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)  # 1-based within document
    seq: Mapped[int | None] = mapped_column(Integer)  # 1-based within case (docket page number)
    image_sha256: Mapped[str | None] = mapped_column(String(64))
    image_blob_key: Mapped[str | None] = mapped_column(Text)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    dpi: Mapped[int | None] = mapped_column(Integer)
    text_layer_status: Mapped[str | None] = mapped_column(String(16))  # none|unicode|legacy|broken
    text_layer_report: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    ocr_engine: Mapped[str | None] = mapped_column(String(32))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_blob_key: Mapped[str | None] = mapped_column(Text)  # restricted: un-redacted lines + bboxes
    ocr_status: Mapped[str] = mapped_column(String(16), server_default="pending")
    text_redacted: Mapped[str | None] = mapped_column(Text)
    text_redacted_ascii: Mapped[str | None] = mapped_column(Text)
    text_sha256: Mapped[str | None] = mapped_column(String(64))  # of the normalised, pre-redaction text
    pii_counts: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    classification: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    predicted_type: Mapped[str | None] = mapped_column(String(48))
    predicted_confidence: Mapped[float | None] = mapped_column(Float)
    pipeline_version: Mapped[str | None] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    document: Mapped[Document] = relationship(back_populates="pages")
    __table_args__ = (
        UniqueConstraint("document_id", "page_no", name="uq_pages_doc_page"),
        Index("ix_pages_case_seq", "case_id", "seq"),
    )


class Exhibit(Base):
    __tablename__ = "exhibits"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    exhibit_type: Mapped[str] = mapped_column(ForeignKey("exhibit_types.code"))
    stage: Mapped[str] = mapped_column(String(24))
    start_page_seq: Mapped[int] = mapped_column(Integer)
    end_page_seq: Mapped[int] = mapped_column(Integer)
    confidence: Mapped[float | None] = mapped_column(Float)
    classification_method: Mapped[str | None] = mapped_column(String(16))
    origin: Mapped[str] = mapped_column(String(16), server_default="auto")  # auto | manual
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    doc_date: Mapped[dt.date | None] = mapped_column(Date)
    extraction: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    pages: Mapped[list["ExhibitPage"]] = relationship(cascade="all, delete-orphan", order_by="ExhibitPage.position")
    __table_args__ = (
        CheckConstraint("stage IN ('filing','query_response','internal','final')", name="ck_exhibits_stage"),
        Index("ix_exhibits_case_seq", "case_id", "seq"),
    )


class ExhibitPage(Base):
    __tablename__ = "exhibit_pages"
    exhibit_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("exhibits.id", ondelete="CASCADE"))
    page_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("pages.id", ondelete="CASCADE"))
    position: Mapped[int] = mapped_column(Integer)
    __table_args__ = (PrimaryKeyConstraint("exhibit_id", "page_id"),)


# ---------------------------------------------------------------------------------------------
# Case cards and derived rows
# ---------------------------------------------------------------------------------------------
class CaseCard(Base):
    __tablename__ = "case_cards"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    version: Mapped[int] = mapped_column(Integer)
    schema_version: Mapped[str] = mapped_column(String(16), server_default="card.v1")
    status: Mapped[str] = mapped_column(String(16), server_default="draft")
    card: Mapped[dict[str, Any]] = mapped_column(JSONB)
    content_hash: Mapped[str] = mapped_column(String(64))
    locked: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    approved_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    parent_card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_cards.id"))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        UniqueConstraint("case_id", "version", name="uq_case_cards_case_version"),
        CheckConstraint("status IN ('draft','approved','superseded')", name="ck_case_cards_status"),
    )


class CaseRequirement(Base):
    __tablename__ = "case_requirements"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_cards.id", ondelete="CASCADE"))
    requirement_code: Mapped[str] = mapped_column(ForeignKey("requirement_catalog.code"))
    status: Mapped[str] = mapped_column(String(16))
    satisfied_by_exhibit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exhibits.id", ondelete="SET NULL"))
    was_queried_in: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exhibits.id", ondelete="SET NULL"))
    evidence: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    origin: Mapped[str] = mapped_column(String(16), server_default="auto")
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        UniqueConstraint("card_id", "requirement_code", name="uq_case_requirements_card_code"),
        CheckConstraint(
            "status IN ('present','missing','invalid','queried','waived','not_applicable')",
            name="ck_case_requirements_status",
        ),
    )


class CaseEvent(Base):
    __tablename__ = "case_events"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_cards.id", ondelete="CASCADE"))
    event_code: Mapped[str] = mapped_column(ForeignKey("event_catalog.code"))
    seq: Mapped[int] = mapped_column(Integer)
    event_date: Mapped[dt.date | None] = mapped_column(Date)
    exhibit_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("exhibits.id", ondelete="SET NULL"))
    evidence: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    origin: Mapped[str] = mapped_column(String(16), server_default="auto")
    created_at: Mapped[dt.datetime] = _created()


class CaseEmbedding(Base):
    __tablename__ = "case_embeddings"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_cards.id", ondelete="CASCADE"))
    facet: Mapped[str] = mapped_column(String(16))  # facts | documents | issues
    model: Mapped[str] = mapped_column(String(64))
    as_of: Mapped[dt.date | None] = mapped_column(Date)
    source_text: Mapped[str] = mapped_column(Text)
    embedding: Mapped[list[float]] = mapped_column(Vector(EMBEDDING_DIM))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        UniqueConstraint("card_id", "facet", "model", name="uq_case_embeddings_card_facet_model"),
        CheckConstraint("facet IN ('facts','documents','issues')", name="ck_case_embeddings_facet"),
    )


# ---------------------------------------------------------------------------------------------
# Rules layer (Phase 2) and ASR (Phase 2) / fees (Phase 3)
# ---------------------------------------------------------------------------------------------
class RuleSet(Base):
    __tablename__ = "rule_sets"
    id: Mapped[uuid.UUID] = _pk()
    code: Mapped[str] = mapped_column(String(64), unique=True)
    name: Mapped[str] = mapped_column(Text)
    notification_ref: Mapped[str | None] = mapped_column(Text)
    notification_date: Mapped[dt.date | None] = mapped_column(Date)
    effective_from: Mapped[dt.date] = mapped_column(Date)
    effective_to: Mapped[dt.date | None] = mapped_column(Date)
    kind: Mapped[str] = mapped_column(String(24), server_default="base")  # base | amendment | amnesty | circular
    supersedes_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sets.id"))
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    source_blob_key: Mapped[str | None] = mapped_column(Text)
    source_filename: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = untouched seed row
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_rule_sets_status"),)


class RulePremiumSlab(Base):
    __tablename__ = "rule_premium_slabs"
    id: Mapped[uuid.UUID] = _pk()
    rule_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sets.id", ondelete="CASCADE"))
    applicant_category: Mapped[str] = mapped_column(String(32))  # CHS
    holding_basis: Mapped[str] = mapped_column(String(32))  # occupancy_class_2 | govt_lease
    land_use: Mapped[str] = mapped_column(String(32))
    window_from: Mapped[dt.date | None] = mapped_column(Date)
    window_to: Mapped[dt.date | None] = mapped_column(Date)
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))  # null until read from the notification
    rate_basis: Mapped[str] = mapped_column(String(48))
    min_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    max_amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    source_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    seed_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = untouched seed row
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_rule_premium_slabs_status"),)


class RuleRequirement(Base):
    __tablename__ = "rule_requirements"
    id: Mapped[uuid.UUID] = _pk()
    rule_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sets.id", ondelete="CASCADE"))
    requirement_code: Mapped[str] = mapped_column(ForeignKey("requirement_catalog.code"))
    mandatory: Mapped[bool] = mapped_column(Boolean, server_default=text("true"))
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    source_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    seed_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = untouched seed row
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_rule_requirements_status"),)


class RuleObligation(Base):
    __tablename__ = "rule_obligations"
    id: Mapped[uuid.UUID] = _pk()
    rule_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sets.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    description_mr: Mapped[str | None] = mapped_column(Text)
    description_en: Mapped[str | None] = mapped_column(Text)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    source_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    seed_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = untouched seed row
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_rule_obligations_status"),)


class RuleAdjustment(Base):
    __tablename__ = "rule_adjustments"
    id: Mapped[uuid.UUID] = _pk()
    rule_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sets.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    kind: Mapped[str] = mapped_column(String(16))  # discount | surcharge | interest | penalty | rebate
    rate_pct: Mapped[Decimal | None] = mapped_column(Numeric(7, 4))
    amount: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    applies_from: Mapped[dt.date | None] = mapped_column(Date)
    applies_to: Mapped[dt.date | None] = mapped_column(Date)
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    source_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    seed_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = untouched seed row
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (CheckConstraint(VERIFICATION, name="ck_rule_adjustments_status"),)


class RuleDocumentCheck(Base):
    """What one filed document must show under a rule set, in plain language; the intake scrutiny asks the model to
    check it (rci.intake.extract) and the report cites the row."""

    __tablename__ = "rule_document_checks"
    id: Mapped[uuid.UUID] = _pk()
    rule_set_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("rule_sets.id", ondelete="CASCADE"))
    code: Mapped[str] = mapped_column(String(64))
    slot: Mapped[str] = mapped_column(String(32))  # rci.intake.slots key
    text_en: Mapped[str] = mapped_column(Text)
    text_mr: Mapped[str | None] = mapped_column(Text)
    severity: Mapped[str] = mapped_column(String(16), server_default="problem")
    condition: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    source_ref: Mapped[str | None] = mapped_column(Text)
    notes: Mapped[str | None] = mapped_column(Text)
    seed_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    verified_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    updated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))  # null = untouched seed row
    updated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (
        CheckConstraint(VERIFICATION, name="ck_rule_document_checks_status"),
        CheckConstraint("severity IN ('problem','warning','info')", name="ck_rule_document_checks_severity"),
    )


class AsrRate(Base):
    __tablename__ = "asr_rates"
    id: Mapped[uuid.UUID] = _pk()
    year: Mapped[int] = mapped_column(Integer)  # ASR year (1 April start)
    division: Mapped[str | None] = mapped_column(Text)
    district: Mapped[str] = mapped_column(Text)
    taluka: Mapped[str | None] = mapped_column(Text)
    village: Mapped[str] = mapped_column(Text)
    zone: Mapped[str | None] = mapped_column(String(32))
    sub_zone: Mapped[str | None] = mapped_column(String(32))
    cts_range: Mapped[str | None] = mapped_column(Text)
    land_rate_per_sqm: Mapped[Decimal] = mapped_column(Numeric(14, 2))
    residential_rate_per_sqm: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    commercial_rate_per_sqm: Mapped[Decimal | None] = mapped_column(Numeric(14, 2))
    source_ref: Mapped[str | None] = mapped_column(Text)
    status: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    import_batch_id: Mapped[uuid.UUID | None] = mapped_column(UUID(as_uuid=True))
    imported_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    imported_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        Index("ix_asr_rates_lookup", "year", "district", "village", "zone"),
        CheckConstraint(VERIFICATION, name="ck_asr_rates_status"),
    )


class FeeCalculation(Base):
    __tablename__ = "fee_calculations"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_cards.id"))
    rule_set_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("rule_sets.id"))
    engine_version: Mapped[str] = mapped_column(String(32))
    inputs: Mapped[dict[str, Any]] = mapped_column(JSONB)
    trace: Mapped[list[Any]] = mapped_column(JSONB)
    total_amount: Mapped[Decimal | None] = mapped_column(Numeric(16, 2))
    status: Mapped[str] = mapped_column(String(16), server_default="draft")  # computed | unverified | blocked
    depends_on_unverified: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    params: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # fee parameters used (with statuses)
    issues: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # {"blockers": [...], "unverified": [...]}
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        CheckConstraint("status IN ('draft','computed','unverified','blocked')", name="ck_fee_calculations_status"),
        Index("ix_fee_calculations_case", "case_id", "created_at"),
    )


class CaseAnalysis(Base):
    """A stored run of POST /cases/{id}/analyse: precedents, gaps, next steps and the fee trace (Phase 3)."""

    __tablename__ = "case_analyses"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_cards.id", ondelete="CASCADE"))
    as_of: Mapped[dt.date] = mapped_column(Date)  # time-travel date for the precedent pool
    rule_date: Mapped[dt.date | None] = mapped_column(Date)  # date that selected the rule stack
    engine_version: Mapped[str] = mapped_column(String(32))
    config_hash: Mapped[str] = mapped_column(String(16))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    result: Mapped[dict[str, Any]] = mapped_column(JSONB)
    fee_calculation_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("fee_calculations.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (Index("ix_case_analyses_case", "case_id", "created_at"),)


# ---------------------------------------------------------------------------------------------
# Drafting (Phase 4)
# ---------------------------------------------------------------------------------------------
class Clause(Base):
    """Clause library (Phase 4). Rows are mined from precedent letters / orders (status candidate, one row per distinct
    delexicalised wording, clustered by embedding), seeded (origin seed, canonical, to_verify) or curated (canonical
    wording with Jinja slots picked by an officer). Drafts use canonical rows only."""

    __tablename__ = "clause_library"
    id: Mapped[uuid.UUID] = _pk()
    clause_key: Mapped[str] = mapped_column(String(96))  # e.g. query_item.PROPERTY_CARD_RECENT, order.operative
    kind: Mapped[str] = mapped_column(String(32))  # scrutiny_note | query_letter | demand_letter | collector_order
    text_mr: Mapped[str] = mapped_column(Text)  # source wording (mined: delexicalised text; seed / curated: example render)
    template: Mapped[str | None] = mapped_column(Text)  # Jinja template with slots (canonical rows)
    slots: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    rule_set_codes: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # empty = any rule stack
    conditions: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    source_case_ids: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    source_refs: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # [{case_id, case_ref, exhibit_id, page_seq}]
    cluster_id: Mapped[int | None] = mapped_column(Integer)
    member_count: Mapped[int] = mapped_column(Integer, server_default="1")
    canonical: Mapped[bool] = mapped_column(Boolean, server_default=text("false"))
    status: Mapped[str] = mapped_column(String(16), server_default="candidate")  # candidate | canonical | rejected
    data_class: Mapped[str] = mapped_column(String(16), server_default="any")  # real | synthetic | any (seed wording)
    origin: Mapped[str] = mapped_column(String(16), server_default="mined")  # seed | mined | curated
    verification: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    seed_key: Mapped[str | None] = mapped_column(String(96), unique=True)
    text_hash: Mapped[str | None] = mapped_column(String(64))
    flags: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # has_pii, needs_slots, ...
    notes: Mapped[str | None] = mapped_column(Text)
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    curated_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    curated_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()
    __table_args__ = (
        UniqueConstraint("data_class", "kind", "text_hash", name="uq_clause_library_mined"),
        CheckConstraint("status IN ('candidate','canonical','rejected')", name="ck_clause_library_status"),
        CheckConstraint("data_class IN ('real','synthetic','any')", name="ck_clause_library_data_class"),
        CheckConstraint("verification IN ('verified','to_verify')", name="ck_clause_library_verification"),
        Index("ix_clause_library_key", "kind", "clause_key"),
    )


class Draft(Base):
    """One rendered draft (Phase 4). Each generation writes two rows with the same version: `internal` (provenance
    notes + watermark) and `issue` (clean). The officer's edited upload and its diff are kept on the issue row."""

    __tablename__ = "drafts"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    template_code: Mapped[str] = mapped_column(String(48))  # SCRUTINY_NOTE | QUERY_LETTER | DEMAND_LETTER | COLLECTOR_ORDER
    version: Mapped[int] = mapped_column(Integer)
    render_mode: Mapped[str] = mapped_column(String(16))  # internal | issue
    content: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    blob_key: Mapped[str | None] = mapped_column(Text)
    sha256: Mapped[str | None] = mapped_column(String(64))
    analysis_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_analyses.id", ondelete="SET NULL"))
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("eval_runs.id", ondelete="SET NULL"))  # backtest run
    drafting_version: Mapped[str | None] = mapped_column(String(32))
    officer_upload_blob_key: Mapped[str | None] = mapped_column(Text)
    officer_upload_sha256: Mapped[str | None] = mapped_column(String(64))
    officer_diff: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    uploaded_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    uploaded_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    status: Mapped[str] = mapped_column(String(16), server_default="generated")  # generated | incomplete | officer_edited | superseded
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        UniqueConstraint("case_id", "template_code", "version", "render_mode", name="uq_drafts_version"),
        CheckConstraint("status IN ('generated','incomplete','officer_edited','superseded')", name="ck_drafts_status"),
        CheckConstraint("render_mode IN ('internal','issue')", name="ck_drafts_render_mode"),
        Index("ix_drafts_case", "case_id", "template_code", "version"),
    )


# ---------------------------------------------------------------------------------------------
# PII vault, audit, pipeline bookkeeping
# ---------------------------------------------------------------------------------------------
class PiiToken(Base):
    __tablename__ = "pii_tokens"
    __table_args__ = (
        UniqueConstraint("case_id", "entity_type", "value_hmac", name="uq_pii_tokens_value"),
        UniqueConstraint("case_id", "token", name="uq_pii_tokens_token"),
        {"schema": "vault"},
    )
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    token: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str] = mapped_column(String(32))
    value_hmac: Mapped[bytes] = mapped_column(LargeBinary)
    ciphertext: Mapped[bytes] = mapped_column(LargeBinary)
    nonce: Mapped[bytes] = mapped_column(LargeBinary)
    key_id: Mapped[str] = mapped_column(String(32))
    created_at: Mapped[dt.datetime] = _created()


class AuditLog(Base):
    __tablename__ = "audit_log"
    id: Mapped[int] = mapped_column(BigInteger, primary_key=True, autoincrement=True)
    occurred_at: Mapped[dt.datetime] = _created()
    actor_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    actor_kind: Mapped[str] = mapped_column(String(16))  # user | system | worker | cli
    action: Mapped[str] = mapped_column(String(64))
    entity_type: Mapped[str | None] = mapped_column(String(48))
    entity_id: Mapped[str | None] = mapped_column(String(64))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="SET NULL"))
    details: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    ip: Mapped[str | None] = mapped_column(String(64))
    __table_args__ = (Index("ix_audit_log_case", "case_id", "occurred_at"),)


class PipelineStep(Base):
    """One row per (case, scope, step). `input_hash` + `pipeline_version` decide whether a re-run
    can skip the step (idempotency on sha256 of the inputs)."""

    __tablename__ = "pipeline_steps"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    scope: Mapped[str] = mapped_column(String(80))  # document sha256 or 'case'
    step: Mapped[str] = mapped_column(String(32))
    input_hash: Mapped[str] = mapped_column(String(64))
    pipeline_version: Mapped[str] = mapped_column(String(32))
    status: Mapped[str] = mapped_column(String(16))
    output: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    error: Mapped[str | None] = mapped_column(Text)
    runs: Mapped[int] = mapped_column(Integer, server_default="1")
    started_at: Mapped[dt.datetime] = _created()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    __table_args__ = (UniqueConstraint("case_id", "scope", "step", name="uq_pipeline_steps_scope_step"),)


# ---------------------------------------------------------------------------------------------
# Evaluation (Phase 1 OCR bench, Phase 2 corrections/agreement, Phase 3 tuning, Phase 4 backtest)
# ---------------------------------------------------------------------------------------------
class EvalRun(Base):
    __tablename__ = "eval_runs"
    id: Mapped[uuid.UUID] = _pk()
    kind: Mapped[str] = mapped_column(String(32))  # ocr_bench | extraction_accuracy | agreement | loo_tuning | fee_golden | holdout_selection | backtest
    config: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    status: Mapped[str] = mapped_column(String(16), server_default="running")
    code_version: Mapped[str | None] = mapped_column(String(64))
    report_blob_key: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created()
    finished_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))


class EvalCaseResult(Base):
    __tablename__ = "eval_case_results"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"))
    case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    stratum: Mapped[str | None] = mapped_column(String(64))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    outputs: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    __table_args__ = (Index("ix_eval_case_results_run", "run_id"),)


class OcrBenchmarkResult(Base):
    __tablename__ = "ocr_benchmark_results"
    id: Mapped[uuid.UUID] = _pk()
    run_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("eval_runs.id", ondelete="CASCADE"))
    sample_id: Mapped[str] = mapped_column(String(128))
    exhibit_type: Mapped[str] = mapped_column(String(48))
    engine: Mapped[str] = mapped_column(String(32))
    ref_chars: Mapped[int] = mapped_column(Integer)
    edit_distance: Mapped[int] = mapped_column(Integer)
    cer: Mapped[float] = mapped_column(Float)
    mean_confidence: Mapped[float | None] = mapped_column(Float)
    latency_ms: Mapped[int | None] = mapped_column(Integer)


class EvalLabel(Base):
    """Officer labels for the backtest (Phase 4). One current label per (case, kind, key, labeller):
    precedent_relevance  case = holdout, target = precedent, key = target case id, value {relevance: 0|1|2}
    fp_adjudication      case = holdout, key = requirement code, value {verdict: truly_unnecessary|valid_not_asked}
    draft_rating         case = drafted case, key = template code, value {rating: 1-5, usability, draft_id, comment}"""

    __tablename__ = "eval_labels"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    target_case_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    label_kind: Mapped[str] = mapped_column(String(32))  # precedent_relevance | fp_adjudication | draft_rating
    label_key: Mapped[str] = mapped_column(String(96), server_default="")
    value: Mapped[dict[str, Any]] = mapped_column(JSONB)
    run_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("eval_runs.id", ondelete="SET NULL"))
    labeller_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()
    __table_args__ = (
        UniqueConstraint("case_id", "label_kind", "label_key", "labeller_id", name="uq_eval_labels_key"),
        CheckConstraint("label_kind IN ('precedent_relevance','fp_adjudication','draft_rating')", name="ck_eval_labels_kind"),
    )


class ExtractionCorrection(Base):
    __tablename__ = "extraction_corrections"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    draft_card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_cards.id", ondelete="CASCADE"))
    approved_card_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("case_cards.id", ondelete="CASCADE"))
    exhibit_type: Mapped[str | None] = mapped_column(String(48))
    field_path: Mapped[str] = mapped_column(Text)
    draft_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    approved_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB)
    action: Mapped[str] = mapped_column(String(16))  # accept | edit | not_present | added | segment
    draft_method: Mapped[str | None] = mapped_column(String(16))  # rules | llm | rules+llm (of the draft value)
    review_assignment_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("review_assignments.id", ondelete="SET NULL"))
    created_by: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("users.id"))
    created_at: Mapped[dt.datetime] = _created()


class ReviewAssignment(Base):
    __tablename__ = "review_assignments"
    id: Mapped[uuid.UUID] = _pk()
    case_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("cases.id", ondelete="CASCADE"))
    reviewer_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("users.id"))
    mode: Mapped[str] = mapped_column(String(16), server_default="single")  # single | double
    round: Mapped[int] = mapped_column(Integer, server_default="1")  # review round of the case (re-review = 2, ...)
    base_card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_cards.id"))  # draft the review started from
    card_id: Mapped[uuid.UUID | None] = mapped_column(ForeignKey("case_cards.id"))  # approved card (if this one approved)
    status: Mapped[str] = mapped_column(String(16), server_default="assigned")  # assigned | submitted | approved | closed
    working: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # live review state (review/state.py)
    submitted: Mapped[dict[str, Any] | None] = mapped_column(JSONB)  # frozen snapshot for the agreement report
    submitted_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()
    __table_args__ = (
        UniqueConstraint("case_id", "round", "reviewer_id", name="uq_review_assignments_case_round_reviewer"),
        CheckConstraint("mode IN ('single','double')", name="ck_review_assignments_mode"),
        CheckConstraint("status IN ('assigned','submitted','approved','closed')", name="ck_review_assignments_status"),
    )


# ---------------------------------------------------------------------------------------------
# GR library (Part 2): department documents (public), separate from cases
# ---------------------------------------------------------------------------------------------
class LibraryDocument(Base):
    """One department document (GR, gazette notification, circular, letter, case note, court order), keyed by the
    inventory (config/library/inventory.yaml). The metadata columns hold the inventory's / the model's proposal until the
    operator confirms them (`verification` verified). `proposed` keeps the model's values with their evidence (page,
    quote, anchor) and flags (ungrounded fields)."""

    __tablename__ = "library_documents"
    id: Mapped[uuid.UUID] = _pk()
    key: Mapped[str] = mapped_column(String(96), unique=True)
    sha256: Mapped[str] = mapped_column(String(64), unique=True)
    filename: Mapped[str] = mapped_column(Text)
    source_path: Mapped[str] = mapped_column(Text)
    aliases: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    blob_key: Mapped[str] = mapped_column(Text)
    page_count: Mapped[int | None] = mapped_column(Integer)
    status: Mapped[str] = mapped_column(String(16), server_default="registered")  # registered | processed | failed
    error: Mapped[str | None] = mapped_column(Text)
    ingest_signature: Mapped[str | None] = mapped_column(Text)
    doc_type: Mapped[str | None] = mapped_column(String(24))
    doc_date: Mapped[dt.date | None] = mapped_column(Date)
    doc_number: Mapped[str | None] = mapped_column(Text)
    issuing_desk: Mapped[str | None] = mapped_column(Text)
    subject_mr: Mapped[str | None] = mapped_column(Text)
    subject_en: Mapped[str | None] = mapped_column(Text)
    topics: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    related_cases: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    cited_by: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    proposed: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")
    verification: Mapped[str] = mapped_column(String(16), server_default="to_verify")
    verified_at: Mapped[dt.datetime | None] = mapped_column(DateTime(timezone=True))
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    pages: Mapped[list["LibraryPage"]] = relationship(back_populates="document", order_by="LibraryPage.page_no",
                                                      passive_deletes=True)
    __table_args__ = (
        CheckConstraint("verification IN ('verified','to_verify')", name="ck_library_documents_verification"),
        CheckConstraint("status IN ('registered','processed','failed')", name="ck_library_documents_status"),
        Index("ix_library_documents_date", "doc_date"),
    )


class LibraryPage(Base):
    __tablename__ = "library_pages"
    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_documents.id", ondelete="CASCADE"))
    page_no: Mapped[int] = mapped_column(Integer)
    image_blob_key: Mapped[str | None] = mapped_column(Text)
    width_px: Mapped[int | None] = mapped_column(Integer)
    height_px: Mapped[int | None] = mapped_column(Integer)
    dpi: Mapped[int | None] = mapped_column(Integer)
    text_layer_status: Mapped[str | None] = mapped_column(String(16))
    geometry_source: Mapped[str | None] = mapped_column(String(16))  # text_layer | image | tesseract | azure_di
    ocr_engine: Mapped[str | None] = mapped_column(String(32))
    ocr_model: Mapped[str | None] = mapped_column(String(64))
    ocr_confidence: Mapped[float | None] = mapped_column(Float)
    ocr_blob_key: Mapped[str | None] = mapped_column(Text)
    text: Mapped[str | None] = mapped_column(Text)  # normalise_text of the OCR lines
    text_ascii: Mapped[str | None] = mapped_column(Text)  # ASCII-digit shadow (same length)
    flags: Mapped[dict[str, Any]] = mapped_column(JSONB, server_default="{}")  # digit_conflicts, unaligned_lines, tiles, read_failed
    created_at: Mapped[dt.datetime] = _created()
    updated_at: Mapped[dt.datetime] = _updated()

    document: Mapped[LibraryDocument] = relationship(back_populates="pages")
    __table_args__ = (UniqueConstraint("document_id", "page_no", name="uq_library_pages_doc_page"),)


class LibraryProvision(Base):
    """The search unit: a paragraph / clause / table row / ... of a library document, with its text spans on the pages
    (offsets into library_pages.text) and one anchor (boxes on the page image) per span."""

    __tablename__ = "library_provisions"
    id: Mapped[uuid.UUID] = _pk()
    document_id: Mapped[uuid.UUID] = mapped_column(ForeignKey("library_documents.id", ondelete="CASCADE"))
    seq: Mapped[int] = mapped_column(Integer)
    label: Mapped[str] = mapped_column(Text)
    kind: Mapped[str] = mapped_column(String(16))  # title|preamble|reference|paragraph|clause|table_row|order|annex|signature
    heading_path: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    text: Mapped[str] = mapped_column(Text)
    text_ascii: Mapped[str] = mapped_column(Text)
    page_from: Mapped[int] = mapped_column(Integer)
    page_to: Mapped[int] = mapped_column(Integer)
    spans: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")  # [{page_no, start, end}]
    anchors: Mapped[list[Any]] = mapped_column(JSONB, server_default="[]")
    tokens: Mapped[list[str]] = mapped_column(ARRAY(Text), server_default="{}")
    embedding: Mapped[list[float] | None] = mapped_column(Vector(EMBEDDING_DIM))
    embedding_model: Mapped[str | None] = mapped_column(String(64))
    created_at: Mapped[dt.datetime] = _created()
    __table_args__ = (
        UniqueConstraint("document_id", "seq", name="uq_library_provisions_doc_seq"),
        Index("ix_library_provisions_tokens", "tokens", postgresql_using="gin"),
    )
