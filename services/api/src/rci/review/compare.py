"""Comparisons over review outcomes:

* corrections(): machine draft vs approved values, per exhibit field (→ extraction_corrections, and the
  field-level extraction accuracy report).
* comparable() / agreement(): two independent reviews of the same case (double-review mode) → agreement on
  segmentation, stages, field values, requirement statuses, query mappings and events; aggregate() pools
  cases and adds Cohen's kappa on requirement statuses.
"""
from __future__ import annotations

from collections import Counter
from typing import Any

from rci.pipeline.extract.common import jsonable
from rci.pipeline.extract.fields import FIELDS


def _val(ev: Any) -> Any:
    if ev is None:
        return None
    if isinstance(ev, list):
        vals = [jsonable(e.get("value")) for e in ev if e]
        return vals or None
    return jsonable(ev.get("value"))


def _method(ev: Any) -> str | None:
    if isinstance(ev, list):
        return ev[0].get("method") if ev else None
    return ev.get("method") if isinstance(ev, dict) else None


def _overlap(a: dict, b: dict) -> int:
    return max(0, min(a["end"], b["end"]) - max(a["start"], b["start"]) + 1)


def align(draft_exhibits: list[dict], exhibit: dict) -> dict | None:
    """The draft exhibit of the same type with the largest page overlap."""
    cands = [(d, _overlap(d, exhibit)) for d in draft_exhibits if d["type"] == exhibit["type"]]
    cands = [c for c in cands if c[1] > 0]
    return max(cands, key=lambda c: c[1])[0] if cands else None


def corrections(draft_exhibits: list[dict], approved_exhibits: list[dict], approved_fields: dict[str, dict]) -> list[dict]:
    """Rows for extraction_corrections. action: accept | edit | not_present | added (fields), segment (boundaries/type), stage."""
    rows = []
    draft_keys = {(d["type"], d["start"], d["end"]) for d in draft_exhibits}
    for e in approved_exhibits:
        d = align(draft_exhibits, e)
        if (e["type"], e["start"], e["end"]) not in draft_keys:
            rows.append({"exhibit_type": e["type"], "field_path": f"{e['type']}._segment", "action": "segment",
                         "draft_value": {"type": d["type"], "pages": [d["start"], d["end"]]} if d else None,
                         "approved_value": {"type": e["type"], "pages": [e["start"], e["end"]]}, "draft_method": None})
        elif d and d["stage"] != e["stage"]:
            rows.append({"exhibit_type": e["type"], "field_path": f"{e['type']}._stage", "action": "edit",
                         "draft_value": {"value": d["stage"]}, "approved_value": {"value": e["stage"]}, "draft_method": None})
        dfields = (d or {}).get("fields") or {}
        afields = approved_fields.get(e["exhibit_id"], {})
        for spec in FIELDS.get(e["type"], []):
            dv, av = _val(dfields.get(spec.name)), _val(afields.get(spec.name))
            if dv is None and av is None:
                continue
            if dv == av:
                action = "accept"
            elif av is None:
                action = "not_present"
            elif dv is None:
                action = "added"
            else:
                action = "edit"
            rows.append({"exhibit_type": e["type"], "field_path": f"{e['type']}.{spec.name}", "action": action,
                         "draft_value": {"value": dv, "page_seq": _page(dfields.get(spec.name))} if dv is not None else None,
                         "approved_value": {"value": av, "page_seq": _page(afields.get(spec.name))} if av is not None else None,
                         "draft_method": _method(dfields.get(spec.name))})
    return rows


def _page(ev: Any) -> int | None:
    if isinstance(ev, list):
        return ev[0].get("page_seq") if ev else None
    return ev.get("page_seq") if isinstance(ev, dict) else None


def accuracy_report(rows: list[dict]) -> dict:
    """rows: {exhibit_type, field_path, action, draft_method}. precision = correct / draft had a value;
    recall = correct / approved has a value; accuracy = correct / all compared."""
    def metrics(c: Counter) -> dict:
        acc, edit, notp, added = c["accept"], c["edit"], c["not_present"], c["added"]
        n = acc + edit + notp + added
        return {"n": n, "accept": acc, "edit": edit, "not_present": notp, "added": added,
                "precision": round(acc / (acc + edit + notp), 4) if acc + edit + notp else None,
                "recall": round(acc / (acc + edit + added), 4) if acc + edit + added else None,
                "accuracy": round(acc / n, 4) if n else None}

    structural = ("._segment", "._stage")
    field_rows = [r for r in rows if r["action"] in ("accept", "edit", "not_present", "added")
                  and not r["field_path"].endswith(structural)]
    by_field: dict[str, Counter] = {}
    by_type: dict[str, Counter] = {}
    by_method: dict[str, Counter] = {}
    for r in field_rows:
        by_field.setdefault(r["field_path"], Counter())[r["action"]] += 1
        by_type.setdefault(r["exhibit_type"] or "?", Counter())[r["action"]] += 1
        by_method.setdefault(r.get("draft_method") or "none", Counter())[r["action"]] += 1
    seg = [r for r in rows if r["action"] == "segment"]
    stage = [r for r in rows if r["field_path"].endswith("._stage")]
    return {
        "overall": metrics(Counter(r["action"] for r in field_rows)),
        "by_field": {k: metrics(v) for k, v in sorted(by_field.items())},
        "by_exhibit_type": {k: metrics(v) for k, v in sorted(by_type.items())},
        "by_draft_method": {k: metrics(v) for k, v in sorted(by_method.items())},
        "segmentation_corrections": len(seg),
        "stage_corrections": len(stage),
    }


# ------------------------------------------------------------------------------------------------
# Double review
# ------------------------------------------------------------------------------------------------
def comparable(state: dict, fields: dict[str, dict], requirements: list[dict], events: list[dict]) -> dict:
    """A reviewer-independent view of one review (keys are page ranges, not exhibit ids)."""
    ex = {e["exhibit_id"]: e for e in state["exhibits"]}

    def key(e: dict) -> str:
        return f"{e['type']}:{e['start']}-{e['end']}"

    qmap = {}
    for k, m in state["query_map"].items():
        eid, idx = k.split("#")
        if eid in ex:
            qmap[f"{ex[eid]['start']}#{idx}"] = "NOT_A_REQUIREMENT" if m["not_requirement"] else sorted(m["codes"])
    return {
        "exhibits": {key(e): e["stage"] for e in state["exhibits"]},
        "fields": {key(ex[eid]): {name: _val(v) for name, v in fs.items()} for eid, fs in fields.items() if eid in ex},
        "requirements": {r["code"]: r["status"] for r in requirements},
        "query_map": qmap,
        "events": sorted(f"{e['code']}@{e['date']}" for e in events),
    }


def _ratio(num: int, den: int) -> float | None:
    return round(num / den, 4) if den else None


def agreement(a: dict, b: dict) -> dict:
    ea, eb = set(a["exhibits"]), set(b["exhibits"])
    both = ea & eb
    field_total = field_agree = 0
    disagreements: list[dict] = []
    for k in sorted(both):
        fa, fb = a["fields"].get(k, {}), b["fields"].get(k, {})
        for name in sorted(set(fa) | set(fb)):
            va, vb = fa.get(name), fb.get(name)
            if va is None and vb is None:
                continue
            field_total += 1
            if va == vb:
                field_agree += 1
            else:
                disagreements.append({"kind": "field", "exhibit": k, "field": name, "a": va, "b": vb})
    for k in sorted(ea ^ eb):
        disagreements.append({"kind": "segment", "exhibit": k, "in": "a" if k in ea else "b"})
    stage_agree = sum(1 for k in both if a["exhibits"][k] == b["exhibits"][k])
    for k in sorted(both):
        if a["exhibits"][k] != b["exhibits"][k]:
            disagreements.append({"kind": "stage", "exhibit": k, "a": a["exhibits"][k], "b": b["exhibits"][k]})
    codes = sorted(set(a["requirements"]) | set(b["requirements"]))
    req_agree = sum(1 for c in codes if a["requirements"].get(c) == b["requirements"].get(c))
    disagreements += [{"kind": "requirement", "code": c, "a": a["requirements"].get(c), "b": b["requirements"].get(c)}
                      for c in codes if a["requirements"].get(c) != b["requirements"].get(c)]
    qk = sorted(set(a["query_map"]) | set(b["query_map"]))
    q_agree = sum(1 for k in qk if a["query_map"].get(k) == b["query_map"].get(k))
    disagreements += [{"kind": "query_map", "item": k, "a": a["query_map"].get(k), "b": b["query_map"].get(k)}
                      for k in qk if a["query_map"].get(k) != b["query_map"].get(k)]
    eva, evb = set(a["events"]), set(b["events"])
    disagreements += [{"kind": "event", "event": e, "in": "a" if e in eva else "b"} for e in sorted(eva ^ evb)]
    return {
        "segmentation": _ratio(len(both), len(ea | eb)),
        "stage": _ratio(stage_agree, len(both)),
        "fields": _ratio(field_agree, field_total), "fields_compared": field_total, "fields_agree": field_agree,
        "requirements": _ratio(req_agree, len(codes)),
        "query_mapping": _ratio(q_agree, len(qk)), "query_items_compared": len(qk), "query_items_agree": q_agree,
        "events": _ratio(len(eva & evb), len(eva | evb)),
        "disagreements": disagreements,
    }


def cohen_kappa(pairs: list[tuple[str, str]]) -> float | None:
    if not pairs:
        return None
    n = len(pairs)
    po = sum(1 for x, y in pairs if x == y) / n
    ca, cb = Counter(x for x, _ in pairs), Counter(y for _, y in pairs)
    pe = sum(ca[k] * cb[k] for k in set(ca) | set(cb)) / (n * n)
    return round((po - pe) / (1 - pe), 4) if pe < 1 else 1.0


def aggregate(cases: list[dict]) -> dict:
    """cases: [{case_ref, a: comparable, b: comparable}] → pooled agreement + per-case rows."""
    per_case, pairs = [], []
    totals = Counter()
    for c in cases:
        ag = agreement(c["a"], c["b"])
        per_case.append({"case_ref": c["case_ref"], "case_id": c.get("case_id"), "reviewers": c.get("reviewers"),
                         **{k: v for k, v in ag.items() if k != "disagreements"}, "disagreement_count": len(ag["disagreements"]),
                         "disagreements": ag["disagreements"]})
        codes = set(c["a"]["requirements"]) | set(c["b"]["requirements"])
        pairs += [(c["a"]["requirements"].get(k, "-"), c["b"]["requirements"].get(k, "-")) for k in sorted(codes)]
        ea, eb = set(c["a"]["exhibits"]), set(c["b"]["exhibits"])
        totals["seg_both"] += len(ea & eb)
        totals["seg_union"] += len(ea | eb)
        totals["fields_compared"] += ag["fields_compared"]
        totals["fields_agree"] += ag["fields_agree"]
        totals["q_compared"] += ag["query_items_compared"]
        totals["q_agree"] += ag["query_items_agree"]
    return {
        "cases": len(cases),
        "segmentation": _ratio(totals["seg_both"], totals["seg_union"]),
        "fields": _ratio(totals["fields_agree"], totals["fields_compared"]), "fields_compared": totals["fields_compared"],
        "requirements_agreement": _ratio(sum(1 for x, y in pairs if x == y), len(pairs)),
        "requirements_kappa": cohen_kappa(pairs),
        "query_mapping": _ratio(totals["q_agree"], totals["q_compared"]),
        "per_case": per_case,
    }
