"""phase 2: review state, rule verification bookkeeping, ASR import batches, auth

Additive only: new columns, nullable rate_pct (a slab can exist before its rate is read from the
notification), review-assignment working state.

Revision ID: 0002
Revises: 0001
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0002"
down_revision: Union[str, None] = "0001"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

RULE_CHILDREN = ("rule_premium_slabs", "rule_requirements", "rule_obligations", "rule_adjustments")


def _verification_columns(table: str) -> None:
    op.add_column(table, sa.Column("verified_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column(table, sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column(table, sa.Column("updated_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.add_column(table, sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True))


def upgrade() -> None:
    op.add_column("users", sa.Column("last_login_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("requirement_catalog", sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))

    op.add_column("rule_sets", sa.Column("source_filename", sa.Text(), nullable=True))
    _verification_columns("rule_sets")
    for t in RULE_CHILDREN:
        op.add_column(t, sa.Column("notes", sa.Text(), nullable=True))
        op.add_column(t, sa.Column("seed_key", sa.String(96), nullable=True))
        op.create_unique_constraint(f"uq_{t}_seed_key", t, ["seed_key"])
        _verification_columns(t)
    op.alter_column("rule_premium_slabs", "rate_pct", existing_type=sa.Numeric(7, 4), nullable=True)

    op.add_column("asr_rates", sa.Column("import_batch_id", sa.UUID(), nullable=True))
    op.add_column("asr_rates", sa.Column("imported_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))

    op.add_column("extraction_corrections", sa.Column("draft_method", sa.String(16), nullable=True))
    op.add_column("extraction_corrections", sa.Column(
        "review_assignment_id", sa.UUID(), sa.ForeignKey("review_assignments.id", ondelete="SET NULL"), nullable=True))

    op.add_column("review_assignments", sa.Column("round", sa.Integer(), server_default="1", nullable=False))
    op.add_column("review_assignments", sa.Column("base_card_id", sa.UUID(), sa.ForeignKey("case_cards.id"), nullable=True))
    op.add_column("review_assignments", sa.Column(
        "working", postgresql.JSONB(astext_type=sa.Text()), server_default="{}", nullable=False))
    op.add_column("review_assignments", sa.Column("submitted", postgresql.JSONB(astext_type=sa.Text()), nullable=True))
    op.add_column("review_assignments", sa.Column("submitted_at", sa.DateTime(timezone=True), nullable=True))
    op.add_column("review_assignments", sa.Column(
        "updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False))
    op.create_unique_constraint("uq_review_assignments_case_round_reviewer", "review_assignments", ["case_id", "round", "reviewer_id"])
    op.create_check_constraint("ck_review_assignments_mode", "review_assignments", "mode IN ('single','double')")
    op.create_check_constraint("ck_review_assignments_status", "review_assignments",
                               "status IN ('assigned','submitted','approved','closed')")


def downgrade() -> None:
    op.drop_constraint("ck_review_assignments_status", "review_assignments", type_="check")
    op.drop_constraint("ck_review_assignments_mode", "review_assignments", type_="check")
    op.drop_constraint("uq_review_assignments_case_round_reviewer", "review_assignments", type_="unique")
    for c in ("updated_at", "submitted_at", "submitted", "working", "base_card_id", "round"):
        op.drop_column("review_assignments", c)
    op.drop_column("extraction_corrections", "review_assignment_id")
    op.drop_column("extraction_corrections", "draft_method")
    op.drop_column("asr_rates", "imported_by")
    op.drop_column("asr_rates", "import_batch_id")
    op.execute("DELETE FROM rule_premium_slabs WHERE rate_pct IS NULL")
    op.alter_column("rule_premium_slabs", "rate_pct", existing_type=sa.Numeric(7, 4), nullable=False)
    for t in RULE_CHILDREN + ("rule_sets",):
        for c in ("updated_at", "updated_by", "verified_at", "verified_by"):
            op.drop_column(t, c)
    for t in RULE_CHILDREN:
        op.drop_constraint(f"uq_{t}_seed_key", t, type_="unique")
        op.drop_column(t, "seed_key")
        op.drop_column(t, "notes")
    op.drop_column("rule_sets", "source_filename")
    op.drop_column("requirement_catalog", "created_by")
    op.drop_column("users", "last_login_at")
