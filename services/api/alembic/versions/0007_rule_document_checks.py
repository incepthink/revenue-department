"""rules at intake: per-document checks under a rule set, and the intake slot a requirement is filed under

rule_document_checks holds what one filed document must show (plain language, checked by the intake scrutiny);
requirement_catalog.intake_slot maps a requirement to the upload slot that satisfies it, so the rules in force decide
which documents are required. Additive only.

Revision ID: 0007
Revises: 0006
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision: str = "0007"
down_revision: Union[str, None] = "0006"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("requirement_catalog", sa.Column("intake_slot", sa.String(32), nullable=True))
    op.create_table(
        "rule_document_checks",
        sa.Column("id", sa.UUID(), primary_key=True, server_default=sa.text("gen_random_uuid()")),
        sa.Column("rule_set_id", sa.UUID(), sa.ForeignKey("rule_sets.id", ondelete="CASCADE"), nullable=False),
        sa.Column("code", sa.String(64), nullable=False),
        sa.Column("slot", sa.String(32), nullable=False),
        sa.Column("text_en", sa.Text(), nullable=False),
        sa.Column("text_mr", sa.Text(), nullable=True),
        sa.Column("severity", sa.String(16), nullable=False, server_default="problem"),
        sa.Column("condition", JSONB(), nullable=False, server_default="{}"),
        sa.Column("status", sa.String(16), nullable=False, server_default="to_verify"),
        sa.Column("source_ref", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("seed_key", sa.String(96), nullable=True),
        sa.Column("verified_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("verified_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("updated_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=True),
        sa.UniqueConstraint("seed_key", name="uq_rule_document_checks_seed_key"),
        sa.CheckConstraint("status IN ('verified','to_verify')", name="ck_rule_document_checks_status"),
        sa.CheckConstraint("severity IN ('problem','warning','info')", name="ck_rule_document_checks_severity"),
    )
    op.create_index("ix_rule_document_checks_rule_set", "rule_document_checks", ["rule_set_id"])


def downgrade() -> None:
    op.drop_index("ix_rule_document_checks_rule_set", table_name="rule_document_checks")
    op.drop_table("rule_document_checks")
    op.drop_column("requirement_catalog", "intake_slot")
