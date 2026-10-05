"""phase 3: stored case analyses, fee-calculation parameters / issues

Additive only.

Revision ID: 0003
Revises: 0002
Create Date: 2026-09-26
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision: str = "0003"
down_revision: Union[str, None] = "0002"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

JSONB = postgresql.JSONB(astext_type=sa.Text())


def upgrade() -> None:
    op.add_column("fee_calculations", sa.Column("params", JSONB, server_default="{}", nullable=False))
    op.add_column("fee_calculations", sa.Column("issues", JSONB, server_default="{}", nullable=False))
    op.add_column("fee_calculations", sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True))
    op.create_check_constraint("ck_fee_calculations_status", "fee_calculations",
                               "status IN ('draft','computed','unverified','blocked')")
    op.create_index("ix_fee_calculations_case", "fee_calculations", ["case_id", "created_at"])

    op.create_table(
        "case_analyses",
        sa.Column("id", sa.UUID(), server_default=sa.text("gen_random_uuid()"), primary_key=True),
        sa.Column("case_id", sa.UUID(), sa.ForeignKey("cases.id", ondelete="CASCADE"), nullable=False),
        sa.Column("card_id", sa.UUID(), sa.ForeignKey("case_cards.id", ondelete="CASCADE"), nullable=False),
        sa.Column("as_of", sa.Date(), nullable=False),
        sa.Column("rule_date", sa.Date(), nullable=True),
        sa.Column("engine_version", sa.String(32), nullable=False),
        sa.Column("config_hash", sa.String(16), nullable=False),
        sa.Column("embedding_model", sa.String(64), nullable=True),
        sa.Column("result", JSONB, nullable=False),
        sa.Column("fee_calculation_id", sa.UUID(), sa.ForeignKey("fee_calculations.id", ondelete="SET NULL"), nullable=True),
        sa.Column("created_by", sa.UUID(), sa.ForeignKey("users.id"), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
    )
    op.create_index("ix_case_analyses_case", "case_analyses", ["case_id", "created_at"])


def downgrade() -> None:
    op.drop_index("ix_case_analyses_case", table_name="case_analyses")
    op.drop_table("case_analyses")
    op.drop_index("ix_fee_calculations_case", table_name="fee_calculations")
    op.drop_constraint("ck_fee_calculations_status", "fee_calculations", type_="check")
    op.drop_column("fee_calculations", "created_by")
    op.drop_column("fee_calculations", "issues")
    op.drop_column("fee_calculations", "params")
