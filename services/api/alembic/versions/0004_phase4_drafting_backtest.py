"""phase 4: clause library curation columns, draft provenance / officer uploads, keyed eval labels

Additive only.

Revision ID: 0004
Revises: 0003
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0004"
down_revision: Union[str, None] = "0003"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    # clause library
    op.add_column("clause_library", sa.Column("data_class", sa.String(16), server_default="any", nullable=False))
    op.add_column("clause_library", sa.Column("origin", sa.String(16), server_default="mined", nullable=False))
    op.add_column("clause_library", sa.Column("verification", sa.String(16), server_default="to_verify", nullable=False))
    op.add_column("clause_library", sa.Column("seed_key", sa.String(96), nullable=True))
    op.add_column("clause_library", sa.Column("text_hash", sa.String(64), nullable=True))
    op.add_column("clause_library", sa.Column("source_refs", JSONB, server_default="[]", nullable=False))
    op.add_column("clause_library", sa.Column("member_count", sa.Integer(), server_default="1", nullable=False))
    op.add_column("clause_library", sa.Column("flags", JSONB, server_default="{}", nullable=False))
    op.add_column("clause_library", sa.Column("notes", sa.Text(), nullable=True))
    op.add_column("clause_library", sa.Column("curated_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("clause_library", sa.Column("curated_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("clause_library", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_unique_constraint("uq_clause_library_seed_key", "clause_library", ["seed_key"])
    op.create_unique_constraint("uq_clause_library_mined", "clause_library", ["data_class", "kind", "text_hash"])
    op.create_check_constraint("ck_clause_library_status", "clause_library", "status IN ('candidate','canonical','rejected')")
    op.create_check_constraint("ck_clause_library_data_class", "clause_library", "data_class IN ('real','synthetic','any')")
    op.create_check_constraint("ck_clause_library_verification", "clause_library", "verification IN ('verified','to_verify')")
    op.create_index("ix_clause_library_key", "clause_library", ["kind", "clause_key"])

    # drafts
    op.add_column("drafts", sa.Column("analysis_id", sa.UUID(), sa.ForeignKey("case_analyses.id", ondelete="SET NULL"), nullable=True))
    op.add_column("drafts", sa.Column("run_id", sa.UUID(), sa.ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("drafts", sa.Column("drafting_version", sa.String(32), nullable=True))
    op.add_column("drafts", sa.Column("sha256", sa.String(64), nullable=True))
    op.add_column("drafts", sa.Column("officer_upload_sha256", sa.String(64), nullable=True))
    op.add_column("drafts", sa.Column("uploaded_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column("drafts", sa.Column("uploaded_at", sa.DateTime(timezone=True), nullable=True))
    op.create_check_constraint("ck_drafts_status", "drafts", "status IN ('generated','incomplete','officer_edited','superseded')")
    op.create_check_constraint("ck_drafts_render_mode", "drafts", "render_mode IN ('internal','issue')")
    op.create_index("ix_drafts_case", "drafts", ["case_id", "template_code", "version"])

    # eval labels: one current label per (case, kind, key, labeller)
    op.add_column("eval_labels", sa.Column("label_key", sa.String(96), server_default="", nullable=False))
    op.add_column("eval_labels", sa.Column("run_id", sa.UUID(), sa.ForeignKey("eval_runs.id", ondelete="SET NULL"), nullable=True))
    op.add_column("eval_labels", sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_unique_constraint("uq_eval_labels_key", "eval_labels", ["case_id", "label_kind", "label_key", "labeller_id"])
    op.create_check_constraint("ck_eval_labels_kind", "eval_labels",
                               "label_kind IN ('precedent_relevance','fp_adjudication','draft_rating')")
    op.create_index("ix_eval_case_results_run", "eval_case_results", ["run_id"])


def downgrade() -> None:
    op.drop_index("ix_eval_case_results_run", table_name="eval_case_results")
    op.drop_constraint("ck_eval_labels_kind", "eval_labels", type_="check")
    op.drop_constraint("uq_eval_labels_key", "eval_labels", type_="unique")
    for c in ("updated_at", "run_id", "label_key"):
        op.drop_column("eval_labels", c)
    op.drop_index("ix_drafts_case", table_name="drafts")
    op.drop_constraint("ck_drafts_render_mode", "drafts", type_="check")
    op.drop_constraint("ck_drafts_status", "drafts", type_="check")
    for c in ("uploaded_at", "uploaded_by", "officer_upload_sha256", "sha256", "drafting_version", "run_id", "analysis_id"):
        op.drop_column("drafts", c)
    op.drop_index("ix_clause_library_key", table_name="clause_library")
    for ck in ("ck_clause_library_verification", "ck_clause_library_data_class", "ck_clause_library_status"):
        op.drop_constraint(ck, "clause_library", type_="check")
    op.drop_constraint("uq_clause_library_mined", "clause_library", type_="unique")
    op.drop_constraint("uq_clause_library_seed_key", "clause_library", type_="unique")
    for c in ("updated_at", "curated_at", "curated_by", "notes", "flags", "member_count", "source_refs", "text_hash", "seed_key",
              "verification", "origin", "data_class"):
        op.drop_column("clause_library", c)
