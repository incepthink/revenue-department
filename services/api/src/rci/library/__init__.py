"""GR library (Part 2): department documents read with geometry, split into provisions, searched with cited answers.

  inventory   walk docs/provided-by-department, dedupe, propose metadata → config/library/inventory.yaml
  reader      page geometry (text layer | page image | engine boxes) + the GPT line reader
  ingest      inventory entries → library_documents / library_pages / library_provisions
  provisions  paragraphs / clauses / table rows with spans and anchors (pure)
  index       tokens + embeddings of provisions
  retrieval   hybrid search (keyword BM25 + vector, RRF, LLM rerank)
  answer      grounded answer with verbatim quotes and a deterministic post-check
"""
