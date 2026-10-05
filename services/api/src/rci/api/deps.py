"""Shared FastAPI dependencies."""
from __future__ import annotations

from typing import Iterator

from sqlalchemy.orm import Session

from rci.db.session import session_factory


def get_session() -> Iterator[Session]:
    s = session_factory()()
    try:
        yield s
        s.commit()
    except Exception:
        s.rollback()
        raise
    finally:
        s.close()
