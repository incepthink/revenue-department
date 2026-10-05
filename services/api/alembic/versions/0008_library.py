"""GR library: department documents, their pages and provisions (the search unit)

library_documents holds one row per unique department PDF (keyed by the inventory, config/library/inventory.yaml), with
metadata that stays to_verify until the operator confirms it. library_pages holds the page image, OCR blob (lines with
boxes from the geometry-first GPT reader) and normalised text. library_provisions holds paragraphs / clauses / table
rows with their spans, anchors, keyword tokens (GIN) and embeddings (HNSW, cosine). Additive only.

Revision ID: 0008
Revises: 0007
Create Date: 2026-10-05
"""
from typing import Sequence, Union

import pgvector.sqlalchemy
import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import ARRAY, JSONB

revision: str = "0008"
down_revision: Union[str, None] = "0007"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        "library_documents",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("key", sa.String(96), nullable=False),
        sa.Column("sha256", sa.String(64), nullable=False),
        sa.Column("filename", sa.Text(), nullable=False),
        sa.Column("source_path", sa.Text(), nullable=False),
        sa.Column("aliases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("blob_key", sa.Text(), nullable=False),
        sa.Column("page_count", sa.Integer(), nullable=True),
        sa.Column("status", sa.String(16), nullable=False, server_default="registered"),
        sa.Column("error", sa.Text(), nullable=True),
        sa.Column("ingest_signature", sa.Text(), nullable=True),
        sa.Column("doc_type", sa.String(24), nullable=True),
        sa.Column("doc_date", sa.Date(), nullable=True),
        sa.Column("doc_number", sa.Text(), nullable=True),
        sa.Column("issuing_desk", sa.Text(), nullable=True),
        sa.Column("subject_mr", sa.Text(), nullable=True),
        sa.Column("subject_en", sa.Text(), nullable=True),
        sa.Column("topics", JSONB(), nullable=False, server_default="[]"),
        sa.Column("related_cases", JSONB(), nullable=False, server_default="[]"),
        sa.Column("cited_by", JSONB(), nullable=False, server_default="[]"),
        sa.Column("proposed", JSONB(), nullable=False, server_default="{}"),
        sa.Column("verification", sa.String(16), nullable=False, server_default="to_verify"),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("key", name="library_documents_key_key"),
        sa.UniqueConstraint("sha256", name="library_documents_sha256_key"),
        sa.CheckConstraint("verification IN ('verified','to_verify')", name="ck_library_documents_verification"),
        sa.CheckConstraint("status IN ('registered','processed','failed')", name="ck_library_documents_status"),
    )
    op.create_index("ix_library_documents_date", "library_documents", ["doc_date"])
    op.create_table(
        "library_pages",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("library_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("page_no", sa.Integer(), nullable=False),
        sa.Column("image_blob_key", sa.Text(), nullable=True),
        sa.Column("width_px", sa.Integer(), nullable=True),
        sa.Column("height_px", sa.Integer(), nullable=True),
        sa.Column("dpi", sa.Integer(), nullable=True),
        sa.Column("text_layer_status", sa.String(16), nullable=True),
        sa.Column("geometry_source", sa.String(16), nullable=True),
        sa.Column("ocr_engine", sa.String(32), nullable=True),
        sa.Column("ocr_model", sa.String(64), nullable=True),
        sa.Column("ocr_confidence", sa.Float(), nullable=True),
        sa.Column("ocr_blob_key", sa.Text(), nullable=True),
        sa.Column("text", sa.Text(), nullable=True),
        sa.Column("text_ascii", sa.Text(), nullable=True),
        sa.Column("flags", JSONB(), nullable=False, server_default="{}"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "page_no", name="uq_library_pages_doc_page"),
    )
    op.create_table(
        "library_provisions",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("document_id", sa.UUID(), sa.ForeignKey("library_documents.id", ondelete="CASCADE"), nullable=False),
        sa.Column("seq", sa.Integer(), nullable=False),
        sa.Column("label", sa.Text(), nullable=False),
        sa.Column("kind", sa.String(16), nullable=False),
        sa.Column("heading_path", JSONB(), nullable=False, server_default="[]"),
        sa.Column("text", sa.Text(), nullable=False),
        sa.Column("text_ascii", sa.Text(), nullable=False),
        sa.Column("page_from", sa.Integer(), nullable=False),
        sa.Column("page_to", sa.Integer(), nullable=False),
        sa.Column("spans", JSONB(), nullable=False, server_default="[]"),
        sa.Column("anchors", JSONB(), nullable=False, server_default="[]"),
        sa.Column("tokens", ARRAY(sa.Text()), nullable=False, server_default="{}"),
        sa.Column("embedding", pgvector.sqlalchemy.vector.VECTOR(dim=1536), nullable=True),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False, server_default=sa.func.now()),
        sa.UniqueConstraint("document_id", "seq", name="uq_library_provisions_doc_seq"),
    )
    op.create_index("ix_library_provisions_tokens", "library_provisions", ["tokens"], postgresql_using="gin")
    op.execute("CREATE INDEX ix_library_provisions_hnsw ON library_provisions USING hnsw (embedding vector_cosine_ops)")


def downgrade() -> None:
    op.execute("DROP INDEX IF EXISTS ix_library_provisions_hnsw")
    op.drop_index("ix_library_provisions_tokens", table_name="library_provisions")
    op.drop_table("library_provisions")
    op.drop_table("library_pages")
    op.drop_index("ix_library_documents_date", table_name="library_documents")
    op.drop_table("library_documents")
