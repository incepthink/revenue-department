"""Redaction: Presidio AnalyzerEngine (with our custom Indian recognisers, no NER model) + structural
name recognisers → overlap resolution → vault tokens.

Presidio runs on the ASCII-digit shadow text; offsets map 1:1 back onto the original text.
"""
from __future__ import annotations

import uuid
from collections import Counter
from dataclasses import dataclass, field
from functools import lru_cache

from rci.pipeline.normalise import ascii_digits
from rci.pipeline.redact.recognisers import PATTERN_RECOGNISERS, Span
from rci.pipeline.redact.structural import find_structural_names
from rci.pipeline.redact.vault import Vault

LANG = "mr"
MIN_SCORE = 0.5

_CONTEXT_WORDS = {
    "AADHAAR": ["आध\u093eर", "aadhaar", "uid"],
    "AADHAAR_VID": ["vid", "आभ\u093eस\u0940"],
    "PHONE": ["म\u094bब\u093eईल", "म\u094b", "mobile", "phone", "स\u0902पर\u094dक"],
    "PAN": ["pan", "प\u0945न"],
    "EMAIL": ["email", "\u0908-\u092e\u0947ल"],
    "BANK_ACCOUNT": ["ख\u093eत\u0947", "account", "ब\u0901क", "ifsc"],
}


@lru_cache
def _presidio_analyzer():
    """Build a Presidio AnalyzerEngine whose NLP engine is a blank multilingual spaCy tokenizer: there is
    no usable Marathi NER model, all detection comes from our recognisers."""
    import spacy
    from presidio_analyzer import AnalysisExplanation, AnalyzerEngine, EntityRecognizer, RecognizerRegistry, RecognizerResult
    from presidio_analyzer.nlp_engine import NlpArtifacts, NlpEngine

    class BlankNlpEngine(NlpEngine):
        def __init__(self):
            self.nlp = spacy.blank("xx")

        def load(self):
            pass

        def is_loaded(self):
            return True

        def process_text(self, text, language):
            doc = self.nlp(text)
            return NlpArtifacts(
                entities=[], tokens=doc, tokens_indices=[t.idx for t in doc],
                lemmas=[t.text.lower() for t in doc], nlp_engine=self, language=language,
            )

        def process_batch(self, texts, language, batch_size=1, n_process=1, **kwargs):
            for t in texts:
                yield t, self.process_text(t, language)

        def is_stopword(self, word, language):
            return False

        def is_punct(self, word, language):
            return False

        def get_supported_entities(self):
            return []

        def get_supported_languages(self):
            return [LANG]

    class FnRecognizer(EntityRecognizer):
        def __init__(self, entity, fn):
            self.fn = fn
            super().__init__(supported_entities=[entity], supported_language=LANG, name=f"rci_{entity.lower()}",
                             context=_CONTEXT_WORDS.get(entity, []))

        def load(self):
            pass

        def analyze(self, text, entities, nlp_artifacts=None):
            return [
                RecognizerResult(s.entity, s.start, s.end, s.score,
                                 analysis_explanation=AnalysisExplanation(self.name, s.score, textual_explanation=s.source),
                                 recognition_metadata={"recognizer_name": self.name, "source": s.source})
                for s in self.fn(text)
            ]

    registry = RecognizerRegistry(supported_languages=[LANG])
    for entity, fn in PATTERN_RECOGNISERS.items():
        registry.add_recognizer(FnRecognizer(entity, fn))
    return AnalyzerEngine(registry=registry, nlp_engine=BlankNlpEngine(), supported_languages=[LANG])


def detect_pattern_pii(text: str) -> list[Span]:
    shadow = ascii_digits(text)
    results = _presidio_analyzer().analyze(text=shadow, language=LANG, score_threshold=MIN_SCORE)
    return [
        Span(r.start, r.end, r.entity_type, r.score, (r.recognition_metadata or {}).get("source", "presidio"))
        for r in results
    ]


def resolve_overlaps(spans: list[Span]) -> list[Span]:
    """Keep a non-overlapping set, preferring higher score, then longer span."""
    chosen: list[Span] = []
    for s in sorted(spans, key=lambda s: (-s.score, -(s.end - s.start), s.start)):
        if all(s.end <= c.start or s.start >= c.end for c in chosen):
            chosen.append(s)
    return sorted(chosen, key=lambda s: s.start)


@dataclass
class RedactionResult:
    text: str
    text_ascii: str
    spans: list[Span] = field(default_factory=list)

    @property
    def counts(self) -> dict[str, int]:
        return dict(Counter(s.entity for s in self.spans))


class Redactor:
    def __init__(self, vault: Vault):
        self.vault = vault

    def detect(self, text: str) -> list[Span]:
        return resolve_overlaps(detect_pattern_pii(text) + [s for s in find_structural_names(text) if s.score >= MIN_SCORE])

    def redact(self, case_id: uuid.UUID, text: str) -> RedactionResult:
        spans = self.detect(text)
        out, cursor = [], 0
        for s in spans:
            out.append(text[cursor : s.start])
            out.append(self.vault.tokenize(case_id, s.entity, text[s.start : s.end]))
            cursor = s.end
        out.append(text[cursor:])
        red = "".join(out)
        return RedactionResult(text=red, text_ascii=ascii_digits(red), spans=spans)

