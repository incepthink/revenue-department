"""single-user platform: open reviews move to the built-in operator user, in single-review mode

The platform now has one user (rci.auth.operator_user) and no login. Reviews that were still open under the old
reviewer / admin accounts are handed to the operator so they can be finished, and switched to single-review mode
(double review needs two people). If a case round had several open reviews, the most recently edited one is kept
and the others are closed. Finished reviews (approved / closed) are left as they were. Data only; no schema change.

Revision ID: 0006
Revises: 0005
Create Date: 2026-09-28
"""
from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: Union[str, None] = "0005"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

OPERATOR_EMAIL = "operator@rci.local"  # rci.auth.OPERATOR_EMAIL


def upgrade() -> None:
    conn = op.get_bind()
    conn.execute(sa.text(
        "INSERT INTO users (id, email, display_name, role, password_hash) "
        "VALUES (gen_random_uuid(), :e, 'Operator', 'admin', NULL) ON CONFLICT (email) DO NOTHING"), {"e": OPERATOR_EMAIL})
    op_id = conn.scalar(sa.text("SELECT id FROM users WHERE email = :e"), {"e": OPERATOR_EMAIL})
    # one open review per case round survives: the operator's own if it has one, else the most recently edited
    conn.execute(sa.text("""
        UPDATE review_assignments SET status = 'closed', updated_at = now()
        WHERE id IN (
            SELECT id FROM (
                SELECT id, row_number() OVER (PARTITION BY case_id, round
                                              ORDER BY (reviewer_id = :op) DESC, updated_at DESC, created_at DESC) AS n
                FROM review_assignments WHERE status IN ('assigned', 'submitted')
            ) ranked WHERE n > 1)"""), {"op": op_id})
    conn.execute(sa.text(
        "UPDATE review_assignments SET reviewer_id = :op, mode = 'single', updated_at = now() "
        "WHERE status IN ('assigned', 'submitted')"), {"op": op_id})


def downgrade() -> None:
    pass  # data handover; the previous owners are in the audit log (review.start)
