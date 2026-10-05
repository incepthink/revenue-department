# packages/schemas

JSON Schemas shared by the API (`services/api`) and the UI (`apps/web`).

| File | What | Source of truth |
|---|---|---|
| `card.v1.json` | Case card: facts with page-referenced evidence, exhibits, validations, draft requirements/events | hand-written; every card is validated against it before it is stored |
| `extraction/<TYPE>.json` | Strict (OpenAI structured-output compatible) schema for LLM extraction of one exhibit type | generated from `rci/pipeline/extract/fields.py` by `make schemas`; a test fails if they drift |

Evidence contract (every fact in a card): `value`, `raw` (verbatim, redacted page text), `page_seq`
(docket page, 1-based across all documents), `doc_page_no`, `document`, `snippet`, `method`
(`rules` | `llm` | `rules+llm` | `manual`), `confidence`, `exhibit_id`, `exhibit_type`, `field`.
