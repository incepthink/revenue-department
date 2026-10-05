"""OCR engines behind one interface + a router with an LLM-vision fallback below a confidence threshold.

Engines: azure_di (Azure AI Document Intelligence Read/Layout — production path for real dockets),
tesseract (offline, synthetic docs + benchmark), llm_vision (fallback), text_layer (not OCR: the
PDF's own Unicode text layer, produced by the pipeline when the legacy-font detector passes it).
"""
from __future__ import annotations

import base64
import io
import logging
import time
from dataclasses import asdict, dataclass, field
from typing import Literal

import httpx

from rci.adapters.llm import DataClass, DataResidencyError, ImagePart, LLMClient
from rci.config import Settings, get_settings

log = logging.getLogger(__name__)

BBox = tuple[float, float, float, float]  # normalised 0..1 (x0, y0, x1, y1)


@dataclass
class OcrWord:
    text: str
    bbox: BBox | None = None
    confidence: float | None = None


@dataclass
class OcrLine:
    text: str
    bbox: BBox | None = None
    confidence: float | None = None
    words: list[OcrWord] = field(default_factory=list)  # un-redacted like `text`: never returned by an API


@dataclass
class OcrResult:
    engine: str
    lines: list[OcrLine]
    mean_confidence: float | None
    raw: dict | None = field(default=None, repr=False)
    fallback_from: str | None = None

    @property
    def text(self) -> str:
        return "\n".join(l.text for l in self.lines)

    def to_json(self) -> dict:
        d = asdict(self)
        d.pop("raw", None)
        return d


class OcrEngine:
    name = "base"

    def allows_real_data(self) -> bool:
        return False

    def recognize(self, png: bytes, *, data_class: DataClass) -> OcrResult:
        if data_class == "real" and not self.allows_real_data():
            raise DataResidencyError(f"OCR engine {self.name} is not approved for real department documents")
        return self._recognize(png, data_class)

    def _recognize(self, png: bytes, data_class: DataClass) -> OcrResult:
        raise NotImplementedError


def _poly_to_bbox(poly: list[float], w: float, h: float) -> BBox:
    xs, ys = poly[0::2], poly[1::2]
    return (min(xs) / w, min(ys) / h, max(xs) / w, max(ys) / h)


class AzureDocumentIntelligenceEngine(OcrEngine):
    name = "azure_di"

    def __init__(self, settings: Settings, http: httpx.Client | None = None):
        if not (settings.azure_di_endpoint and settings.azure_di_key):
            raise RuntimeError("Azure Document Intelligence endpoint/key not configured")
        self.endpoint = settings.azure_di_endpoint.rstrip("/")
        self.key = settings.azure_di_key
        self.model = settings.azure_di_model
        self.api_version = settings.azure_di_api_version
        self.region = settings.azure_di_region.lower()
        self.allowed = {r.lower() for r in settings.allowed_real_data_regions}
        self.http = http or httpx.Client(timeout=60)

    def allows_real_data(self) -> bool:
        return self.region in self.allowed

    def _recognize(self, png: bytes, data_class: DataClass) -> OcrResult:
        url = f"{self.endpoint}/documentintelligence/documentModels/{self.model}:analyze"
        headers = {"Ocp-Apim-Subscription-Key": self.key}
        r = self.http.post(
            url,
            params={"api-version": self.api_version, "features": "languages"} if self.model == "prebuilt-read" else {"api-version": self.api_version},
            headers=headers,
            json={"base64Source": base64.b64encode(png).decode()},
        )
        r.raise_for_status()
        op_url = r.headers["operation-location"]
        deadline = time.monotonic() + 120
        while True:
            pr = self.http.get(op_url, headers=headers)
            pr.raise_for_status()
            body = pr.json()
            if body["status"] == "succeeded":
                break
            if body["status"] == "failed" or time.monotonic() > deadline:
                raise RuntimeError(f"Document Intelligence analyze failed: {body.get('error')}")
            time.sleep(float(pr.headers.get("retry-after", 1)))
        return self.parse_result(body["analyzeResult"])

    @staticmethod
    def parse_result(result: dict) -> OcrResult:
        page = result["pages"][0]
        w, h = float(page["width"]), float(page["height"])
        words = page.get("words", [])
        lines: list[OcrLine] = []
        confs: list[float] = [w_["confidence"] for w_ in words if "confidence" in w_]
        for ln in page.get("lines", []):
            spans = ln.get("spans", [])
            mine = sorted(
                (wd for wd in words if any(s["offset"] <= wd["span"]["offset"] < s["offset"] + s["length"] for s in spans)),
                key=lambda wd: wd["span"]["offset"],
            )
            lc = [wd["confidence"] for wd in mine if "confidence" in wd]
            lines.append(
                OcrLine(
                    text=ln["content"],
                    bbox=_poly_to_bbox(ln["polygon"], w, h) if ln.get("polygon") else None,
                    confidence=sum(lc) / len(lc) if lc else None,
                    words=[OcrWord(wd.get("content", ""), _poly_to_bbox(wd["polygon"], w, h) if wd.get("polygon") else None,
                                   wd.get("confidence")) for wd in mine],
                )
            )
        mean = sum(confs) / len(confs) if confs else None
        return OcrResult(engine="azure_di", lines=lines, mean_confidence=mean, raw=result)


class TesseractEngine(OcrEngine):
    """Offline engine. Never used for real dockets by default (no residency concern, but quality on
    Marathi scans is well below Document Intelligence — see the OCR benchmark)."""

    name = "tesseract"

    def __init__(self, lang: str = "mar+eng"):
        import pytesseract  # noqa: F401  (optional dependency)

        self.lang = lang

    def allows_real_data(self) -> bool:
        return True  # runs locally

    def _recognize(self, png: bytes, data_class: DataClass) -> OcrResult:
        import pytesseract
        from PIL import Image

        img = Image.open(io.BytesIO(png))
        W, H = img.size
        d = pytesseract.image_to_data(img, lang=self.lang, output_type=pytesseract.Output.DICT)
        groups: dict[tuple, list[int]] = {}
        for i, word in enumerate(d["text"]):
            if word.strip():
                groups.setdefault((d["block_num"][i], d["par_num"][i], d["line_num"][i]), []).append(i)
        lines, confs = [], []
        for idx in groups.values():
            x0 = min(d["left"][i] for i in idx)
            y0 = min(d["top"][i] for i in idx)
            x1 = max(d["left"][i] + d["width"][i] for i in idx)
            y1 = max(d["top"][i] + d["height"][i] for i in idx)
            c = [float(d["conf"][i]) / 100 for i in idx if float(d["conf"][i]) >= 0]
            confs += c
            words = [OcrWord(d["text"][i], (d["left"][i] / W, d["top"][i] / H, (d["left"][i] + d["width"][i]) / W,
                                            (d["top"][i] + d["height"][i]) / H),
                             float(d["conf"][i]) / 100 if float(d["conf"][i]) >= 0 else None) for i in idx]
            lines.append(OcrLine(" ".join(d["text"][i] for i in idx), (x0 / W, y0 / H, x1 / W, y1 / H), sum(c) / len(c) if c else None,
                                 words))
        return OcrResult(engine="tesseract", lines=lines, mean_confidence=sum(confs) / len(confs) if confs else None)


VISION_SCHEMA = {
    "type": "object",
    "additionalProperties": False,
    "required": ["lines", "legibility"],
    "properties": {
        "lines": {
            "type": "array",
            "items": {
                "type": "object",
                "additionalProperties": False,
                "required": ["text", "confidence"],
                "properties": {"text": {"type": "string"}, "confidence": {"type": "number"}},
            },
        },
        "legibility": {"type": "number"},
    },
}

VISION_SYSTEM = (
    "You transcribe scanned Maharashtra revenue department documents (Marathi in Devanagari, some English). "
    "Transcribe every visible line exactly, top to bottom, in Unicode. Keep Devanagari digits as written. "
    "Never translate, summarise, correct or invent text. Mark unreadable characters with '�'. "
    "confidence and legibility are 0..1."
)


class LlmVisionEngine(OcrEngine):
    name = "llm_vision"

    def __init__(self, llm: LLMClient):
        self.llm = llm

    def allows_real_data(self) -> bool:
        return self.llm.allows_real_data()

    def _recognize(self, png: bytes, data_class: DataClass) -> OcrResult:
        out = self.llm.complete_json(
            task="ocr_vision",
            system=VISION_SYSTEM,
            user="Transcribe this page.",
            schema=VISION_SCHEMA,
            schema_name="page_transcription",
            data_class=data_class,
            images=[ImagePart(png)],
        )
        lines = [OcrLine(l["text"], None, max(0.0, min(1.0, l["confidence"]))) for l in out["lines"]]
        # Self-reported confidence is not calibrated; cap it so a real engine wins ties.
        mean = min(0.9, max(0.0, float(out["legibility"])))
        return OcrResult(engine="llm_vision", lines=lines, mean_confidence=mean)


@dataclass
class OcrRouter:
    primary: OcrEngine | None
    fallback: OcrEngine | None
    threshold: float = 0.80

    @property
    def available(self) -> bool:
        return self.primary is not None or self.fallback is not None

    def recognize(self, png: bytes, *, data_class: DataClass) -> OcrResult | None:
        first: OcrResult | None = None
        if self.primary is not None:
            first = self.primary.recognize(png, data_class=data_class)
            if first.mean_confidence is not None and first.mean_confidence >= self.threshold:
                return first
        if self.fallback is None:
            return first
        try:
            second = self.fallback.recognize(png, data_class=data_class)
        except Exception as e:  # fallback must never lose the primary result
            log.warning("OCR fallback %s failed: %s", self.fallback.name, e)
            return first
        second.fallback_from = first.engine if first else None
        if first is None or (second.mean_confidence or 0) > (first.mean_confidence or 0):
            return second
        return first


def get_ocr_router(llm: LLMClient, settings: Settings | None = None) -> OcrRouter:
    s = settings or get_settings()
    primary: OcrEngine | None = None
    if s.ocr_primary == "azure_di":
        primary = AzureDocumentIntelligenceEngine(s)
    elif s.ocr_primary == "tesseract":
        primary = TesseractEngine()
    fallback = LlmVisionEngine(llm) if s.ocr_fallback == "llm_vision" and llm.available else None
    return OcrRouter(primary=primary, fallback=fallback, threshold=s.ocr_fallback_threshold)


EngineName = Literal["azure_di", "tesseract", "llm_vision"]
