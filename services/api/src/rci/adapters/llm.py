"""Single LLM client interface: provider = openai (direct OpenAI API, key from OPENAI_API_KEY) (| none, | fake in tests).

Guarantees:
  * Structured output only: every call passes a strict JSON schema and the response is validated
    against it (one repair retry, then LLMOutputError).
  * Data class gate: calls tagged data_class="real" are refused unless the client allows real data.
    The OpenAI client does; the fake test client only when told to.
"""
from __future__ import annotations

import base64
import json
import logging
import threading
from dataclasses import dataclass, field
from typing import Any, Callable, Literal, Sequence

import jsonschema

from rci.config import Settings, get_settings

log = logging.getLogger(__name__)

DataClass = Literal["real", "synthetic", "public"]  # public: published government documents (GRs, gazettes)


class LLMError(RuntimeError):
    pass


class LLMUnavailable(LLMError):
    pass


class DataResidencyError(LLMError):
    pass


class LLMOutputError(LLMError):
    pass


@dataclass
class ImagePart:
    data: bytes
    mime: str = "image/png"

    def data_url(self) -> str:
        return f"data:{self.mime};base64,{base64.b64encode(self.data).decode()}"


def assert_strict_schema(schema: dict, path: str = "$") -> None:
    """OpenAI strict mode: every object lists all properties as required and forbids extras."""
    t = schema.get("type")
    types = t if isinstance(t, list) else [t]
    if "object" in types:
        props = schema.get("properties", {})
        if schema.get("additionalProperties") is not False:
            raise ValueError(f"{path}: additionalProperties must be false")
        if set(schema.get("required", [])) != set(props):
            raise ValueError(f"{path}: all properties must be required")
        for k, v in props.items():
            assert_strict_schema(v, f"{path}.{k}")
    if "array" in types and "items" in schema:
        assert_strict_schema(schema["items"], f"{path}[]")
    for key in ("anyOf", "oneOf"):
        for i, sub in enumerate(schema.get(key, [])):
            assert_strict_schema(sub, f"{path}.{key}[{i}]")


class LLMClient:
    provider: str = "base"

    @property
    def available(self) -> bool:
        return True

    def allows_real_data(self) -> bool:
        return False

    def complete_json(
        self,
        *,
        task: str,
        system: str,
        user: str,
        schema: dict,
        schema_name: str,
        data_class: DataClass,
        images: Sequence[ImagePart] = (),
        temperature: float = 0.0,
    ) -> dict:
        if data_class not in ("real", "synthetic", "public"):
            raise ValueError(f"unknown data_class {data_class!r}")
        if data_class == "real" and not self.allows_real_data():
            raise DataResidencyError(f"provider {self.provider!r} is not approved for real department documents")
        assert_strict_schema(schema)
        messages = [{"role": "system", "content": system}, {"role": "user", "content": self._user_content(user, images)}]
        last_err: Exception | None = None
        for attempt in range(2):
            raw = self._call(messages, schema, schema_name, temperature)
            try:
                out = json.loads(raw)
                jsonschema.validate(out, schema)
                log.info("llm task=%s provider=%s attempt=%d ok", task, self.provider, attempt)
                return out
            except (json.JSONDecodeError, jsonschema.ValidationError) as e:
                last_err = e
                messages = messages + [
                    {"role": "assistant", "content": raw},
                    {"role": "user", "content": f"The output did not match the schema: {e}. Return corrected JSON only."},
                ]
        raise LLMOutputError(f"{task}: invalid structured output after retry: {last_err}")

    @staticmethod
    def _user_content(user: str, images: Sequence[ImagePart]):
        if not images:
            return user
        parts: list[dict[str, Any]] = [{"type": "text", "text": user}]
        parts += [{"type": "image_url", "image_url": {"url": img.data_url(), "detail": "high"}} for img in images]
        return parts

    def _call(self, messages: list, schema: dict, schema_name: str, temperature: float) -> str:
        raise NotImplementedError


class NullLLMClient(LLMClient):
    provider = "none"

    @property
    def available(self) -> bool:
        return False

    def complete_json(self, **kwargs) -> dict:  # type: ignore[override]
        raise LLMUnavailable("no LLM provider configured (RCI_LLM_PROVIDER=none)")


class OpenAIClient(LLMClient):
    provider = "openai"

    def __init__(self, settings: Settings, client=None, model: str | None = None):
        if client is None:
            from openai import OpenAI

            if not settings.openai_api_key:
                raise LLMUnavailable("OPENAI_API_KEY not configured")
            client = OpenAI(api_key=settings.openai_api_key)
        self._client = client
        self._model = model or settings.openai_model
        self._lock = threading.Lock()
        self.usage = {"calls": 0, "prompt_tokens": 0, "completion_tokens": 0}

    @property
    def model(self) -> str:
        return self._model

    def allows_real_data(self) -> bool:
        return True

    def _call(self, messages, schema, schema_name, temperature) -> str:
        # `temperature` is not sent: GPT-5/6 models reject anything but the default (1).
        resp = self._client.chat.completions.create(
            model=self._model,
            messages=messages,
            response_format={"type": "json_schema", "json_schema": {"name": schema_name, "schema": schema, "strict": True}},
        )
        u = getattr(resp, "usage", None)
        with self._lock:
            self.usage["calls"] += 1
            self.usage["prompt_tokens"] += getattr(u, "prompt_tokens", 0) or 0
            self.usage["completion_tokens"] += getattr(u, "completion_tokens", 0) or 0
        choice = resp.choices[0]
        if getattr(choice.message, "refusal", None):
            raise LLMOutputError(f"model refused: {choice.message.refusal}")
        return choice.message.content or ""


@dataclass
class FakeLLMClient(LLMClient):
    """Deterministic client for tests: `responder(task, user, schema)` returns the JSON dict."""

    responder: Callable[[str, str, dict], dict] = lambda task, user, schema: {}
    real_data_ok: bool = False
    calls: list[dict] = field(default_factory=list)
    provider: str = "fake"

    def allows_real_data(self) -> bool:
        return self.real_data_ok

    def complete_json(self, *, task, system, user, schema, schema_name, data_class, images=(), temperature=0.0):
        self._task, self._user = task, user
        self.calls.append({"task": task, "data_class": data_class, "images": len(images)})
        return super().complete_json(
            task=task, system=system, user=user, schema=schema, schema_name=schema_name,
            data_class=data_class, images=images, temperature=temperature,
        )

    def _call(self, messages, schema, schema_name, temperature) -> str:
        return json.dumps(self.responder(self._task, self._user, schema), ensure_ascii=False)


def get_llm_client(settings: Settings | None = None, *, model: str | None = None) -> LLMClient:
    """`model` overrides the configured chat model (the library OCR reader uses settings.openai_ocr_model)."""
    s = settings or get_settings()
    try:
        if s.llm_provider == "openai":
            return OpenAIClient(s, model=model)
    except LLMUnavailable as e:
        log.warning("LLM disabled: %s", e)
    return NullLLMClient()
