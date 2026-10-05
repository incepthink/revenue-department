"""LLM client: data-class gate, strict schemas, validation + retry."""
from types import SimpleNamespace

import pytest

from rci.adapters.llm import (
    DataResidencyError,
    FakeLLMClient,
    LLMOutputError,
    NullLLMClient,
    LLMUnavailable,
    OpenAIClient,
    assert_strict_schema,
    get_llm_client,
)
from rci.adapters.ocr import AzureDocumentIntelligenceEngine, OcrLine, OcrResult, OcrRouter, OcrEngine
from rci.config import Settings

SCHEMA = {"type": "object", "additionalProperties": False, "required": ["x"], "properties": {"x": {"type": "integer"}}}


def call(client, data_class="synthetic"):
    return client.complete_json(task="t", system="s", user="u", schema=SCHEMA, schema_name="t", data_class=data_class)


def test_real_data_refused_unless_client_allows():
    fake = FakeLLMClient(responder=lambda *a: {"x": 1})
    assert call(fake) == {"x": 1}
    with pytest.raises(DataResidencyError):
        call(fake, "real")
    assert call(FakeLLMClient(responder=lambda *a: {"x": 2}, real_data_ok=True), "real") == {"x": 2}


def test_openai_client_uses_configured_model_for_real_data_without_temperature():
    sent = []

    class _Completions:
        def create(self, **kw):
            sent.append(kw)
            msg = SimpleNamespace(content='{"x": 5}', refusal=None)
            return SimpleNamespace(choices=[SimpleNamespace(message=msg)])

    stub = SimpleNamespace(chat=SimpleNamespace(completions=_Completions()))
    c = OpenAIClient(Settings(llm_provider="openai", openai_model="gpt-6-luna"), client=stub)
    assert c.provider == "openai" and c.allows_real_data()
    assert call(c, "real") == {"x": 5}
    assert sent[0]["model"] == "gpt-6-luna" and "temperature" not in sent[0]
    assert sent[0]["response_format"]["json_schema"]["strict"] is True


def test_openai_key_read_from_unprefixed_env(monkeypatch):
    monkeypatch.setenv("OPENAI_API_KEY", "sk-from-env")
    assert Settings(_env_file=None).openai_api_key == "sk-from-env"
    assert get_llm_client(Settings(_env_file=None, llm_provider="openai")).provider == "openai"


def test_unconfigured_provider_falls_back_to_null(monkeypatch):
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("RCI_OPENAI_API_KEY", raising=False)
    c = get_llm_client(Settings(_env_file=None, llm_provider="openai"))
    assert isinstance(c, NullLLMClient) and not c.available
    with pytest.raises(LLMUnavailable):
        call(c)


def test_invalid_output_retries_then_fails():
    answers = iter([{"x": "nope"}, {"x": 3}])
    assert call(FakeLLMClient(responder=lambda *a: next(answers))) == {"x": 3}
    with pytest.raises(LLMOutputError):
        call(FakeLLMClient(responder=lambda *a: {"y": 1}))


def test_strict_schema_enforced():
    with pytest.raises(ValueError):
        assert_strict_schema({"type": "object", "properties": {"a": {"type": "string"}}, "required": []})


def test_extraction_schemas_are_strict_and_in_sync():
    import json

    from rci.config import REPO_ROOT
    from rci.pipeline.extract.extractor import _strip_meta, extraction_schema
    from rci.pipeline.extract.fields import FIELDS

    for t in FIELDS:
        schema = extraction_schema(t)
        assert_strict_schema(_strip_meta(schema))
        on_disk = json.loads((REPO_ROOT / "packages" / "schemas" / "extraction" / f"{t}.json").read_text(encoding="utf-8"))
        assert on_disk == schema, f"{t}: run `rci export-schemas`"


class _Const(OcrEngine):
    def __init__(self, name, conf, real=True):
        self.name, self.conf, self.real = name, conf, real

    def allows_real_data(self):
        return self.real

    def _recognize(self, png, data_class):
        return OcrResult(self.name, [OcrLine("x")], self.conf)


def test_ocr_router_fallback_below_threshold():
    r = OcrRouter(_Const("azure_di", 0.95), _Const("llm_vision", 0.9), 0.8)
    assert r.recognize(b"", data_class="real").engine == "azure_di"
    r = OcrRouter(_Const("azure_di", 0.5), _Const("llm_vision", 0.85), 0.8)
    out = r.recognize(b"", data_class="real")
    assert out.engine == "llm_vision" and out.fallback_from == "azure_di"


def test_ocr_engine_residency_guard():
    with pytest.raises(DataResidencyError):
        _Const("x", 1.0, real=False).recognize(b"", data_class="real")


def test_document_intelligence_result_parsing():
    result = {"pages": [{"width": 1000, "height": 2000,
                         "words": [{"content": "मिळकत", "confidence": 0.9, "span": {"offset": 0, "length": 5}},
                                   {"content": "पत्रिका", "confidence": 0.7, "span": {"offset": 6, "length": 7}}],
                         "lines": [{"content": "म\u093fळकत पत\u094dर\u093fक\u093e", "polygon": [100, 200, 500, 200, 500, 260, 100, 260],
                                    "spans": [{"offset": 0, "length": 13}]}]}]}
    r = AzureDocumentIntelligenceEngine.parse_result(result)
    assert r.text == "मिळकत पत्रिका"
    assert r.lines[0].bbox == (0.1, 0.1, 0.5, 0.13)
    assert r.mean_confidence == pytest.approx(0.8)
