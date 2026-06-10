"""Read an info/stats panel screenshot into structured fields via OCR.

The OCR backend is pluggable (PaddleOCR → EasyOCR → pytesseract, whichever is
installed) so the rest of the app never hard-depends on a heavy CV stack. The
field parser (``parse_info``) is pure text → dict and is unit-tested without OCR.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field


@dataclass
class InfoReadResult:
    fields: dict[str, float] = field(default_factory=dict)
    headlines: list[str] = field(default_factory=list)
    raw_lines: list[str] = field(default_factory=list)
    ocr_backend: str = "none"


# label (lowercased, punctuation-insensitive) → canonical field name
# ``_P`` skips an optional parenthetical right after the label, e.g. "Beta
# (5Y Monthly) 1.10", so we don't grab the digit inside the parentheses.
_P = r"\s*(?:\([^)]*\))?\s*[:\-]?\s*"

_FIELD_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("pe", re.compile(r"p\W*e\s*ratio" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("eps", re.compile(r"\beps\b" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("beta", re.compile(r"\bbeta\b" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("open", re.compile(r"\bopen\b" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("previous_close", re.compile(r"previous\s*close" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("volume", re.compile(r"\bvolume\b" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("market_cap", re.compile(r"market\s*cap" + _P + r"([\d,]+\.?\d*\s*[KMBT]?)", re.I)),
    ("target_1y", re.compile(r"1y?\s*target(?:\s*est)?" + _P + r"([\d,]+\.?\d*)", re.I)),
    ("dividend_yield", re.compile(r"dividend.*?\(?([\d,]+\.?\d*)\s*%", re.I)),
]

_SUFFIX = {"K": 1e3, "M": 1e6, "B": 1e9, "T": 1e12}


def _to_float(token: str) -> float | None:
    token = token.strip().upper().replace(",", "")
    mult = 1.0
    if token and token[-1] in _SUFFIX:
        mult = _SUFFIX[token[-1]]
        token = token[:-1].strip()
    try:
        return float(token) * mult
    except ValueError:
        return None


def parse_info(lines: list[str]) -> tuple[dict[str, float], list[str]]:
    """Extract known numeric fields and likely news headlines from OCR text."""
    text = "\n".join(lines)
    fields: dict[str, float] = {}
    for name, pat in _FIELD_PATTERNS:
        m = pat.search(text)
        if m:
            val = _to_float(m.group(1))
            if val is not None:
                fields[name] = val

    # Headlines: longer text lines with few digits (vs. the numeric stat rows).
    headlines = [
        ln.strip() for ln in lines
        if len(ln.strip()) > 35 and sum(c.isdigit() for c in ln) / max(1, len(ln)) < 0.2
    ]
    return fields, headlines


# --- OCR backends (pluggable, lazy) ------------------------------------------
# Each accepts a path (str/Path) or a BGR numpy array, so the dashboard can pass
# an in-memory image and scripts can pass a filename.
def _as_paddle_input(image):
    return image if hasattr(image, "shape") else str(image)


def _ocr_paddle(image) -> list[str] | None:
    try:
        from paddleocr import PaddleOCR
    except Exception:
        return None
    ocr = PaddleOCR(use_angle_cls=False, lang="en", show_log=False)
    result = ocr.ocr(_as_paddle_input(image), cls=False)
    lines = []
    for page in result or []:
        for _box, (txt, _conf) in page or []:
            lines.append(txt)
    return lines


def _ocr_easyocr(image) -> list[str] | None:
    try:
        import easyocr
    except Exception:
        return None
    reader = easyocr.Reader(["en"], gpu=False)
    return [t for (_b, t, _c) in reader.readtext(_as_paddle_input(image))]


def _ocr_tesseract(image) -> list[str] | None:
    try:
        import pytesseract
        from PIL import Image
    except Exception:
        return None
    try:
        if hasattr(image, "shape"):
            import cv2
            pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        else:
            pil = Image.open(str(image))
        text = pytesseract.image_to_string(pil)
    except Exception:
        return None
    return [ln for ln in text.splitlines() if ln.strip()]


def extract_text(image) -> tuple[list[str], str]:
    """Run the first available OCR backend. Returns (lines, backend_name)."""
    for fn, name in ((_ocr_paddle, "paddleocr"),
                     (_ocr_easyocr, "easyocr"),
                     (_ocr_tesseract, "pytesseract")):
        lines = fn(image)
        if lines is not None:
            return lines, name
    raise RuntimeError(
        "No OCR backend installed. Install one: `pip install paddleocr paddlepaddle` "
        "(recommended), or `pip install easyocr`, or Tesseract + `pip install pytesseract`."
    )


def read_info(image) -> InfoReadResult:
    lines, backend = extract_text(image)
    fields, headlines = parse_info(lines)
    return InfoReadResult(fields, headlines, lines, backend)
