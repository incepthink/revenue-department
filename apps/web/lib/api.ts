// Shared API types (mirror services/api responses; card types mirror packages/schemas/card.v1.json).
export const API_URL = process.env.RCI_API_URL ?? "http://localhost:8000";

export type Evidence = {
  value: unknown;
  raw: string;
  page_seq: number;
  doc_page_no?: number;
  document?: string;
  snippet: string;
  method: string;
  confidence: number;
  exhibit_type?: string;
  field?: string;
  located?: boolean;
  alternatives?: { value: unknown; raw?: string; method: string }[];
};
export type Fact = Evidence | null;

export type CaseSummary = {
  id: string;
  case_ref: string;
  status: string;
  data_class: "real" | "synthetic";
  society_name: string | null;
  filed_on: string | null;
  decided_on: string | null;
  card_version?: number | null;
  card_status?: string | null;
  review?: { mode: string; status: string; round: number; reviewer_id: string }[];
};

export type Validation = {
  code: string;
  status: "pass" | "fail" | "not_evaluable";
  message: string;
  depends_on_unverified: boolean;
  parameters?: { key: string; value: unknown; status: string }[];
};

export type CardV1 = {
  facts: Record<string, Record<string, Fact> | unknown[]>;
  exhibits: { exhibit_id: string; seq: number; type: string; stage: string; page_range: string; confidence: number; doc_date: string | null }[];
  validations: Validation[];
  requirements: { code: string; status: string; was_queried_in: string | null; origin?: string; notes?: string | null }[];
  events: { seq: number; code: string; date: string | null }[];
  summary?: { mr: string; en: string };
  approval?: { approved_by: string; approved_at: string; review_mode: string; base_card_version: number; reviewers?: string[] };
};

export type DocPage = {
  seq: number; page_no: number; ocr_status: string; ocr_engine: string | null; ocr_confidence: number | null;
  width_px: number | null; height_px: number | null;
};

export type CaseDetail = CaseSummary & {
  documents: {
    id: string; seq: number; filename: string; pages: number | null; status: string; error: string | null;
    page_list: DocPage[];
  }[];
  card: { id: string; version: number; status: string; locked: boolean; approved_at: string | null; card: CardV1 } | null;
  card_versions: { version: number; status: string; created_at: string; approved_at: string | null }[];
};

// ---- review view (GET /cases/{id}/review) --------------------------------------------------------
export type Decision = {
  action: "accept" | "edit" | "not_present";
  value?: unknown;
  page_seq?: number;
  raw?: string;
  items?: { raw: string; page_seq: number }[];
};

export type FieldRow = {
  name: string;
  kind: string;
  enum: string[];
  description: string;
  draft: Evidence | Evidence[] | null;
  confidence: number | null;
  decision: Decision | null;
  current: Evidence | Evidence[] | null;
};

export type ReviewExhibit = {
  exhibit_id: string;
  seq: number;
  type: string;
  type_name_mr: string | null;
  stage: string;
  start: number;
  end: number;
  confidence: number;
  method: string;
  origin: string;
  fields: FieldRow[];
  undecided: number;
};

export type Assignment = {
  id: string;
  reviewer: { id: string; display_name: string; email: string };
  mode: "single" | "double";
  round: number;
  status: string;
  submitted_at: string | null;
  base_card_version: number | null;
  mine?: boolean;
};

export type QueryItem = {
  index: number;
  value: string | null;
  raw: string;
  page_seq: number;
  snippet: string;
  suggested_codes: string[];
  mapping: { codes: string[]; not_requirement: boolean } | null;
};

export type ReqRow = {
  code: string;
  name_en: string;
  name_mr: string;
  category: string;
  status: string;
  derived_status: string;
  origin: string;
  satisfied_by_exhibit_id: string | null;
  was_queried_in: string | null;
  notes?: string | null;
  catalog_status: string;
  override: { status: string | null; satisfied_by_exhibit_id: string | null; notes: string | null } | null;
};

export type EventRow = { seq: number; code: string; date: string | null; exhibit_id: string; evidence: Evidence | null; origin?: string };

export type Disagreement = {
  kind: string; exhibit?: string; field?: string; code?: string; item?: string; event?: string; a?: unknown; b?: unknown; in?: string;
};

export type ReviewView = {
  case: { id: string; case_ref: string; status: string; data_class: string; society_name: string | null };
  card: { id: string; version: number; status: string; locked: boolean } | null;
  assignment: Assignment | null;
  assignments: Assignment[];
  double_review: { used: number; first_n: number };
  read_only: boolean;
  can_start: boolean;
  can_reopen: boolean;
  pages?: { seq: number; document: string; page_no: number }[];
  exhibits?: ReviewExhibit[];
  queries?: { exhibit_id: string; letter_date: string | null; ref_no: string | null; items: QueryItem[] }[];
  requirements?: ReqRow[];
  events?: EventRow[];
  events_overridden?: boolean;
  validations?: Validation[];
  progress?: { fields_total: number; fields_undecided: number; query_items_total: number; query_items_unmapped: number };
  rev?: string;
  blockers?: string[];
  can_approve?: boolean;
  can_submit?: boolean;
  disagreements?: {
    segmentation: number | null; fields: number | null; requirements: number | null; query_mapping: number | null;
    events: number | null; disagreements: Disagreement[];
  } | null;
  catalogs?: {
    exhibit_types: { code: string; name_en: string; name_mr: string }[];
    requirements: { code: string; name_en: string; name_mr: string; category: string; status: string }[];
    events: { code: string; name_en: string; name_mr: string }[];
    stages: string[];
  };
};

export type OcrLine = { index: number; bbox: [number, number, number, number] | null; confidence: number | null; text?: string; pii: boolean };
export type PageOcr = {
  seq: number; width_px: number; height_px: number; ocr_engine: string | null; ocr_confidence: number | null;
  aligned: boolean; lines: OcrLine[]; text_redacted: string | null;
};

export const STATUS_COLOUR: Record<string, string> = {
  pass: "var(--ok)", present: "var(--ok)", approved: "var(--ok)", verified: "var(--ok)", resolved: "var(--ok)",
  fail: "var(--bad)", invalid: "var(--bad)", ambiguous: "var(--bad)",
  missing: "var(--warn)", queried: "var(--warn)", to_verify: "var(--warn)", draft: "var(--warn)", unverified: "var(--warn)",
  waived: "var(--muted)", not_applicable: "var(--muted)", not_evaluable: "var(--muted)", superseded: "var(--muted)",
};

export function fmtValue(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "boolean") return v ? "yes" : "no";
  return String(v);
}

// ---- case analysis (POST /cases/{id}/analyse, GET /cases/{id}/analysis) -------------------------------
export type Cite = {
  case_id: string; case_ref: string; exhibit_id: string | null; exhibit_type: string | null; page_seq: number | null;
  snippet?: string | null; field?: string; value?: unknown; how?: string; score?: number; date?: string | null;
};
export type Basis = "STATUTORY" | "PRACTICE" | "PRACTICE_OLD_RULES" | "SUPERSEDED";
export type FeatureRow = { value: number; weight: number; contribution: number };

export type Precedent = {
  rank: number; case_id: string; case_ref: string; card_id: string; society_name: string | null; score: number;
  features: Record<string, FeatureRow>; reasons: { feature: string; kind: "match" | "difference"; en: string; mr: string }[];
  similarity: Record<string, number | null>; tenure: string | null; village: string | null; area_sqm: string | null;
  application_date: string | null; order_date: string | null; as_of: string | null; decision: string | null;
  regime: string[]; procedural_regime: string[]; regime_label: "same_rules" | "older_rules" | "other_rules";
  failed_validations: string[]; open_codes: string[]; summary?: { mr: string; en: string } | null; link: Cite | null;
};

export type Gap = {
  code: string; name_en: string; name_mr: string; category: string; catalog_status: string;
  query_status: "missing" | "invalid" | "queried"; kind: string; basis: Basis; statutory: boolean; reported_because: string[];
  support: number | null; queried_share: number | null; n_applicable: number; n_supporting: number; n_queried: number;
  citations: Cite[]; was_queried_in: Cite[]; query_evidence: Cite[];
  validators: { code: string; message: string; evidence: Cite[] }[];
  rules: { rule_set_code: string; status: string; source_ref: string | null; mandatory: boolean; applies: boolean | null }[];
  explanation_mr: string; explanation_en: string; explanation_source: "llm" | "template";
};

export type StepTrigger = {
  kind: "rule" | "fact"; code: string; rule_set_code?: string; status?: string; applies?: boolean | null;
  text_en?: string | null; text_mr?: string | null; evidence?: Cite[];
};
export type Step = {
  code: string; name_en: string; name_mr: string; actor: string | null; kind: "event" | "check"; status: "done" | "next";
  frequency: number; n_precedents: number; typical_position: number | null; median_days_from_filing: number | null;
  median_days_from_previous: number | null; expected_by: string | null; basis: Basis; triggers: StepTrigger[];
  citations: Cite[]; date?: string | null; own_evidence?: Cite;
};

export type FeeSource = {
  kind: "evidence" | "slab" | "rule_set" | "rule" | "asr"; case_id?: string; page_seq?: number | null; snippet?: string | null;
  exhibit_type?: string | null; value?: unknown; rule_set_code?: string; seed_key?: string | null; status?: string;
  rule_set_status?: string; source_ref?: string | null; code?: string; year?: number; village?: string; rate?: string | null; column?: string;
};
export type FeeStep = {
  step: string; label: string; value: string | null; formula: string | null; inputs: Record<string, unknown>;
  sources: FeeSource[]; status: "ok" | "unverified" | "assumption" | "blocked" | "skipped"; note: string | null;
};
export type Fee = {
  status: "computed" | "unverified" | "blocked"; total: string | null; components: Record<string, string>; trace: FeeStep[];
  blockers: string[]; unverified: string[]; depends_on_unverified: boolean; inputs: Record<string, unknown>;
  params: Record<string, unknown>; engine_version: string; rule_set_code: string | null; calculation_id?: string;
};

export type Analysis = {
  analysis_id?: string; created_at?: string;
  engine: { intel: string; fees: string; config_hash: string; weights_source: "default" | "tuned"; tuned: Record<string, unknown> | null;
            weights: Record<string, number>; support_threshold: number; embedding_model: string | null };
  case: { id: string; case_ref: string; data_class: string; role: string };
  card: { id: string | null; version: number | null; status: string };
  as_of: string; rule_date: string; regime: string[]; procedural_regime: string[];
  query: { society_name: string | null; tenure: string | null; village: string | null; area_sqm: string | null;
           application_date: string | null; failed_validations: string[]; open_codes: string[] };
  pool: Record<string, number>;
  precedents: Precedent[]; gaps: Gap[]; explanations: { source: string; check: string | null };
  steps: { done: Step[]; next: Step[] }; fee: Fee; warnings: string[];
};

export const BASIS_TEXT: Record<Basis, { en: string; mr: string; colour: string }> = {
  STATUTORY: { en: "Statutory", mr: "नियमानुसार", colour: "var(--bad)" },
  PRACTICE: { en: "Practice", mr: "कार्यालयीन प्रथा", colour: "var(--accent)" },
  PRACTICE_OLD_RULES: { en: "Practice (older rules)", mr: "जुन्या नियमांखालील प्रथा", colour: "var(--warn)" },
  SUPERSEDED: { en: "Superseded", mr: "रद्द नियम", colour: "var(--muted)" },
};

// Link to the source-page viewer, highlighting the cited snippet.
export function citeHref(c: { case_id: string; page_seq: number | null | undefined; snippet?: string | null }): string | null {
  if (!c.page_seq) return null;
  const q = c.snippet ? `?q=${encodeURIComponent(c.snippet.slice(0, 160))}` : "";
  return `/cases/${c.case_id}/source/${c.page_seq}${q}`;
}

// Link to the document viewer: a finding's anchor (f, a), a fact of the report (fact), a page (seq), the Text tab.
export function viewerHref(caseId: string, at: { f?: string; a?: number; fact?: number; seq?: number | null; tab?: "pages" | "text" } = {}): string {
  const q = new URLSearchParams();
  if (at.f) q.set("f", at.f);
  if (at.a !== undefined) q.set("a", String(at.a));
  if (at.fact !== undefined) q.set("fact", String(at.fact));
  if (at.seq) q.set("seq", String(at.seq));
  if (at.tab && at.tab !== "pages") q.set("tab", at.tab);
  const qs = q.toString();
  return `/cases/${caseId}/documents${qs ? `?${qs}` : ""}`;
}

// ---- drafting (Phase 4) ---------------------------------------------------------------------------------
export type TemplateCode = "SCRUTINY_NOTE" | "QUERY_LETTER" | "DEMAND_LETTER" | "COLLECTOR_ORDER";
export type DraftDiff = {
  paragraphs: { generated: number; uploaded: number; unchanged: number; changed: number; inserted: number; deleted: number };
  similarity: number; clauses_edited: number; numbers_removed?: string[]; numbers_added?: string[];
  clauses?: { clause_id: string; clause_key: string; kept: boolean }[];
  ops?: { op: string; generated: string[]; uploaded: string[]; similarity: number }[]; filename?: string;
};
export type DraftRow = {
  id: string; case_id: string; template_code: TemplateCode; template_name_mr: string; version: number; render_mode: "internal" | "issue";
  status: "generated" | "incomplete" | "officer_edited" | "superseded"; created_at: string; downloadable: boolean; complete: boolean;
  warnings: string[]; missing_slots: string[]; leak_check: { ok: boolean; found: { kind: string; value: string }[] } | null;
  crosscheck: { where: string; kind: string; value: string; context: string }[]; analysis_id: string | null; run_id: string | null;
  drafting_version: string | null; uploaded_at: string | null; officer_diff: DraftDiff | null;
  paragraphs?: string[]; notes?: { n: number; n_mr: string; text: string }[];
  clauses_used?: { clause_id: string; clause_key: string; origin: string; verification: string; text: string; missing: string[] }[];
  narratives?: { slot: string; source: string; check: string | null }[];
};
export type DraftsView = {
  templates: { code: TemplateCode; kind: string; name_mr: string; name_en: string }[];
  latest: { code?: string; template_code: TemplateCode; version: number; status: string; internal?: DraftRow; issue?: DraftRow }[];
  history: { id: string; template_code: string; version: number; render_mode: string; status: string; created_at: string; uploaded_at: string | null }[];
  my_ratings: Record<string, { rating: number; usability: string; comment: string | null; draft_id: string }>;
};

export type ClauseRow = {
  id: string; clause_key: string; kind: string; text_mr: string; template: string | null; slots: string[]; rule_set_codes: string[];
  conditions: Record<string, unknown>; status: "candidate" | "canonical" | "rejected"; origin: "seed" | "mined" | "curated";
  data_class: string; verification: string; cluster_id: number | null; member_count: number; flags: Record<string, unknown>;
  notes: string | null; source_refs: { case_id: string; case_ref: string; page_seq: number | null }[]; n_sources: number;
  problems: string[];
};
export type ClauseMeta = {
  kinds: string[]; condition_keys: string[]; filters: string[];
  slots: { name: string; description: string; example: unknown }[]; item_slots: { name: string; description: string; example: unknown }[];
};

// ---- backtest (Phase 4) -----------------------------------------------------------------------------------
export type GateCheck = { check: string; value: unknown; rule: string; status: "pass" | "fail" | "skipped"; reason: string };
export type Gate = { passed: boolean; failed: string[]; checks: GateCheck[]; baseline: Record<string, string> | null };
export type BacktestCase = {
  case_id: string; case_ref: string; stratum: Record<string, string>; filing_date: string; rule_date: string;
  predicted: string[]; truth_queried: string[]; truth_first_letter: string[]; statutory_codes: string[]; excluded_gaps: string[];
  predicted_detail: { code: string; name_mr: string; name_en: string; basis: Basis; support: number | null }[];
  steps_predicted: string[]; steps_truth: string[]; steps_checks: string[];
  fee: { world: string; status: string; predicted: string | null; observed: string | null; observed_source: string | null; blockers: string[] };
  top5: { case_id: string; case_ref: string; score: number; society_name: string | null }[];
  drafts?: Record<string, { render_ok: boolean; leak_ok?: boolean; complete?: boolean; missing?: string[]; error?: string }>;
  leaks: { kind: string; case_ref: string }[];
  metrics: Record<string, unknown> & { false_positives: string[]; statutory_false_negatives: string[]; query_precision: number | null;
                                       query_recall: number | null; kendall_tau: number | null; fee_abs_pct_error: number | null };
};
export type BacktestRun = {
  run_id: string; created_at: string; code_version: string;
  config: { data_class: string; holdouts: string[]; holdouts_hash: string; fee_world: string; drafts: string;
            intel: { source: string; fingerprint: string }; report_by: string[] };
  metrics: Record<string, number | null>; metrics_at_run?: Record<string, number | null>;
  strata: Record<string, Record<string, Record<string, number | null>>>; warnings: string[];
  errors: { case_ref: string; error: string }[]; cases: BacktestCase[]; gate: Gate;
};
export type FpItem = {
  case_id: string; case_ref: string; code: string; name_mr: string | null; name_en: string | null; basis: Basis | null; support: number | null;
  n_applicable: number | null; n_supporting: number | null; office_queried: string[]; verdict: string | null; note: string | null;
};

// ---- intake (POST /cases/{id}/intake, GET /cases/{id}/intake[/status]) -------------------------------------
export type IntakeLevel = "problem" | "warning" | "info" | "unchecked" | "ok";
export type IntakeStage = "queued" | "reading" | "read" | "identifying" | "checking" | "checked" | "failed";
export type IntakeRef = {
  slot: string; document_id: string; filename: string; field?: string; label?: string; value?: unknown; raw?: string | null;
  page_seq: number | null; snippet?: string | null;
};
// The rule row a finding rests on (rule_requirements / rule_document_checks); label is set while it is to_verify.
export type IntakeRule = {
  code: string; rule_set_code: string; rule_set_name: string; source_ref: string | null; status: "verified" | "to_verify"; label: string | null;
};
export type IntakeFinding = {
  code: string; level: IntakeLevel; title: string; detail: string; slots: string[]; evidence: IntakeRef[];
  action: string | null; source: "check" | "ai" | "rule"; parameters: { key: string; value: unknown; status: string }[];
  depends_on_unverified: boolean; rule: IntakeRule | null;
  id?: string; anchors?: Anchor[]; // set once the report is anchored (rci.intake.anchors)
};
// Where an evidence item sits on the pages: highlight = boxes over the quote; page = the page only (reason says why);
// missing = no page at all. Boxes are 0..1 of the page image; lines are OCR line indices (the Text tab).
export type Anchor = {
  document_id: string | null; filename: string | null; page_seq: number | null; page_id: string | null; page_no: number | null;
  kind: "highlight" | "page" | "missing"; boxes: [number, number, number, number][]; lines: number[];
  precision: "word" | "line" | null; quote: string | null; reason: null | "no_geometry" | "not_found" | "no_quote";
};
export type IntakeRuleSet = { code: string; kind: string; status: string; effective_from: string; effective_to: string | null };
export type IntakeReport = {
  version: string; verdict: "ready" | "needs_attention" | "incomplete"; headline: string; limited: boolean; llm_provider: string;
  reference_date: string; reference_date_source: string; counts: Record<IntakeLevel, number>; actions: string[];
  facts: (IntakeRef & { fact: string; anchor?: Anchor })[]; findings: IntakeFinding[]; anchors_version?: string;
  rules_applied?: { on: string; rule_sets: IntakeRuleSet[] }; rule_facts?: { tenure: string | null };
};
export type IntakeRuleResult = {
  code: string; text: string; verdict: "met" | "not_met" | "cannot_tell" | "not_applicable";
  quote: string | null; page_seq: number | null; snippet: string | null; reason: string | null;
};
export type IntakeDocResult = {
  slot: string; method: "llm" | "rules" | "none"; readable: boolean; pages: number[];
  document_matches: boolean | null; detected_as: string | null; match_reason: string | null;
  fields: Record<string, Evidence | null>; observations: { severity: "problem" | "warning" | "info"; text: string; page_seq: number | null }[];
  unverified: { field: string; raw: string; reason: string }[]; notes: string[];
  rule_results?: IntakeRuleResult[]; // the rules in force for this slot, as judged on this document
  summary?: string | null; relevance?: "supporting" | "unrelated" | null; // additional documents (slot "other") only
  satisfies_requirement?: string | null; // additional documents: the required document (without a slot) this one is
};
// How the intake identified a file uploaded without a slot (services/api/src/rci/intake/identify.py).
export type IntakeIdentification = {
  slot: string; detected_as?: string | null; reason?: string | null; confidence?: number; method: "llm" | "rules" | "none";
  bundled: string[]; notes: string[];
};
export type IntakeDoc = {
  id: string; slot: string | null; filename: string; pages: number | null; stage: IntakeStage;
  error?: string | null; field_labels?: Record<string, string>; result?: IntakeDocResult | null;
  identified?: IntakeIdentification | null;
};
export type IntakeSlot = { key: string; label: string; label_mr: string; multiple: boolean };
export type IntakeView = {
  case: { id: string; case_ref: string; title: string | null; data_class: string; status: string; last_error: string | null; society_name: string | null };
  state: "not_started" | "processing" | "done" | "failed"; assembling: boolean;
  progress: { total: number; read: number; checked: number }; llm: { provider: string; available: boolean };
  slots: IntakeSlot[]; documents: IntakeDoc[]; report: IntakeReport | null; report_at: string | null;
};
