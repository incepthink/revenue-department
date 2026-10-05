"""intake: the filing slot a document was uploaded for (7/12 extract, property card, …)

Additive only.

Revision ID: 0005
Revises: 0004
Create Date: 2026-09-27
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: Union[str, None] = "0004"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column("documents", sa.Column("slot", sa.String(32), nullable=True))


def downgrade() -> None:
    op.drop_column("documents", "slot")
