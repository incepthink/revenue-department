"""Fee engine, DB side: load the rule stack and ASR rows, run `prepare.calculate`, store a fee_calculations row."""
from __future__ import annotations

import datetime as dt
import uuid
from functools import lru_cache

from sqlalchemy import select
from sqlalchemy.orm import Session

from rci.config import Parameters, get_settings
from rci.db.models import AsrRate, Case, CaseCard, FeeCalculation, RuleSet
from rci.fees.prepare import AsrRow, FeeParams, FeeResult, calculate
from rci.rules import build_stack, load_rows


def load_params() -> FeeParams:
    return FeeParams.from_parameters(Parameters.load(get_settings().fees_file))


def stack_loader(session: Session):
    """date -> RuleStack, loading the rule rows once."""
    rows = load_rows(session)

    @lru_cache(maxsize=64)
    def load(on: dt.date):
        return build_stack(on, *rows)

    return load


def asr_rows_for(session: Session, card: dict) -> list[AsrRow]:
    land = card["facts"].get("land") or {}
    village = (land.get("village") or {}).get("value")
    district = (land.get("district") or {}).get("value")
    if not (village and district):
        return []
    q = select(AsrRate).where(AsrRate.village == village, AsrRate.district == district)
    return [AsrRow(year=r.year, district=r.district, village=r.village, land_rate_per_sqm=r.land_rate_per_sqm,
                   residential_rate_per_sqm=r.residential_rate_per_sqm, commercial_rate_per_sqm=r.commercial_rate_per_sqm,
                   zone=r.zone, status=r.status, source_ref=r.source_ref, id=r.id) for r in session.scalars(q)]


def calculate_for_card(session: Session, card: dict, *, params: FeeParams | None = None, today: dt.date | None = None,
                       loader=None) -> FeeResult:
    return calculate(card, params or load_params(), loader or stack_loader(session), asr_rows_for(session, card),
                     today=today or dt.date.today())


def store(session: Session, case: Case, card_row: CaseCard | None, result: FeeResult, user_id: uuid.UUID | None = None) -> FeeCalculation:
    rs = session.scalar(select(RuleSet).where(RuleSet.code == result.rule_set_code)) if result.rule_set_code else None
    row = FeeCalculation(case_id=case.id, card_id=card_row.id if card_row else None, rule_set_id=rs.id if rs else None,
                         engine_version=result.engine_version, inputs=result.inputs,
                         trace=[t.to_json() for t in result.trace], total_amount=result.total, status=result.status,
                         depends_on_unverified=result.depends_on_unverified, params=result.params,
                         issues={"blockers": result.blockers, "unverified": result.unverified}, created_by=user_id)
    session.add(row)
    session.flush()
    return row
