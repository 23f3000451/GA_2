import os
import re
import json
from datetime import datetime
from typing import Optional

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field, field_validator

# ---------------------------------------------------------------------------
# Optional local LLM (llama-cpp-python). If unavailable, we fall back to a
# robust deterministic extractor so the service still works.
# ---------------------------------------------------------------------------
_llm = None
try:
    from llama_cpp import Llama
    MODEL_PATH = os.getenv("MODEL_PATH", "./models/qwen2.5-1.5b-instruct-q4_k_m.gguf")
    if os.path.exists(MODEL_PATH):
        _llm = Llama(model_path=MODEL_PATH, n_ctx=2048, verbose=False)
except Exception:
    _llm = None


# ---------------------------------------------------------------------------
# Schemas
# ---------------------------------------------------------------------------
class ExtractRequest(BaseModel):
    text: str = Field(..., min_length=1, description="Free-form invoice text")

    @field_validator("text")
    @classmethod
    def not_blank(cls, v: str) -> str:
        if not v or not v.strip():
            raise ValueError("text must not be empty")
        return v


class InvoiceFields(BaseModel):
    vendor: str
    amount: float
    currency: str
    date: str

    @field_validator("currency")
    @classmethod
    def upper_ccy(cls, v: str) -> str:
        v = str(v).strip().upper()
        if not re.fullmatch(r"[A-Z]{3}", v):
            raise ValueError("currency must be 3 uppercase letters")
        return v

    @field_validator("date")
    @classmethod
    def iso_date(cls, v: str) -> str:
        m = re.search(r"(\d{4})-(\d{2})-(\d{2})", str(v))
        if not m:
            raise ValueError("date must contain YYYY-MM-DD")
        return m.group(0)


# ---------------------------------------------------------------------------
# App + CORS
# ---------------------------------------------------------------------------
app = FastAPI(title="Local LLM Invoice Extractor")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,   # must be False when allow_origins=["*"]
    allow_methods=["*"],
    allow_headers=["*"],
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------
CURRENCY_SYMBOLS = {"$": "USD", "€": "EUR", "£": "GBP"}
KNOWN_CURRENCIES = {"USD", "EUR", "GBP"}
COMPANY_SUFFIX = r"(?:Ltd|LLC|Inc|Corp|Corporation|Industries|GmbH|Co\.|Company|Limited|PLC)"

# Tokens that mark the start of another invoice field; used to trim the vendor
FIELD_STOP = r"(?:Total|Amount|Due|Currency|Invoice\s*#|Date|Balance|Payment|Subtotal|Tax)"

PROMPT_TEMPLATE = """You are an information extraction engine.
Extract invoice fields from the text and return ONLY a JSON object with keys:
vendor, amount (number), currency (3-letter uppercase), date (YYYY-MM-DD).

Text:
\"\"\"{text}\"\"\"

JSON:"""


def _clean_vendor(v: str) -> str:
    """Trim trailing field keywords and punctuation from a vendor string."""
    if not v:
        return v
    v = re.split(r"\s+" + FIELD_STOP + r"\b", v, maxsplit=1, flags=re.IGNORECASE)[0]
    return v.strip(" .,;:-\n\t")


def _extract_with_llm(text: str) -> Optional[dict]:
    if _llm is None:
        return None
    try:
        out = _llm(
            PROMPT_TEMPLATE.format(text=text),
            max_tokens=256,
            temperature=0.0,
            stop=["\n\n", "```"],
        )
        raw = out["choices"][0]["text"].strip()
        m = re.search(r"\{.*\}", raw, re.DOTALL)
        if not m:
            return None
        return json.loads(m.group(0))
    except Exception:
        return None


def _extract_deterministic(text: str) -> dict:
    """Regex-based extractor. Handles planted vendors / amounts / dates."""
    t = text

    # ---- currency ----
    currency = None
    m = re.search(r"\b(USD|EUR|GBP)\b", t, re.IGNORECASE)
    if m:
        currency = m.group(1).upper()
    else:
        for sym, code in CURRENCY_SYMBOLS.items():
            if sym in t:
                currency = code
                break
    currency = currency or "USD"

    # ---- amount ----
    amount = None
    for pat in [
        r"(?:total\s+due|amount\s+due|total|balance\s+due|grand\s+total)"
        r"\s*[:\-]?\s*(?:USD|EUR|GBP|\$|€|£)?\s*([0-9][0-9,]*\.?[0-9]*)",
        r"(?:USD|EUR|GBP|\$|€|£)\s*([0-9][0-9,]*\.?[0-9]*)",
        r"([0-9][0-9,]*\.?[0-9]*)\s*(?:USD|EUR|GBP)\b",
    ]:
        m = re.search(pat, t, re.IGNORECASE)
        if m:
            try:
                amount = float(m.group(1).replace(",", ""))
                break
            except ValueError:
                continue
    if amount is None:
        cand = []
        for n in re.findall(r"[0-9][0-9,]*\.?[0-9]*", t):
            try:
                cand.append(float(n.replace(",", "")))
            except ValueError:
                pass
        amount = max(cand) if cand else 0.0

    # ---- date ----
    date = None
    m = re.search(r"(\d{4})-(\d{2})-(\d{2})", t)
    if m:
        date = m.group(0)
    else:
        for fmt, pat in [
            ("%B %d, %Y", r"[A-Za-z]+ \d{1,2},? \d{4}"),
            ("%b %d, %Y", r"[A-Za-z]{3,} \d{1,2},? \d{4}"),
            ("%d/%m/%Y", r"\d{1,2}/\d{1,2}/\d{4}"),
            ("%m/%d/%Y", r"\d{1,2}/\d{1,2}/\d{4}"),
            ("%d %B %Y", r"\d{1,2} [A-Za-z]+ \d{4}"),
            ("%d %b %Y", r"\d{1,2} [A-Za-z]{3,} \d{4}"),
        ]:
            mm = re.search(pat, t)
            if mm:
                try:
                    date = datetime.strptime(mm.group(0), fmt).strftime("%Y-%m-%d")
                    break
                except ValueError:
                    continue
    if date is None:
        date = "1970-01-01"

    # ---- vendor ----
    vendor = None

    # 1) Labelled form: "Vendor: X" / "From: X" / "Billed by: X"
    m = re.search(
        r"(?:vendor|from|billed\s+by|seller|supplier|issuer)\s*[:\-]\s*([^\n,;]+)",
        t,
        re.IGNORECASE,
    )
    if m:
        vendor = _clean_vendor(m.group(1))

    # 2) "Invoice from <Company Suffix>" — non-greedy, stop at punctuation
    if not vendor:
        m = re.search(
            r"(?:invoice|bill|receipt|statement)\s+(?:from|by)\s+"
            r"([A-Z][^\n,;.]{1,80}?" + COMPANY_SUFFIX + r"\.?)",
            t,
            re.IGNORECASE,
        )
        if m:
            vendor = _clean_vendor(m.group(1))

    # 3) Company-suffix form anywhere on a line — non-greedy
    if not vendor:
        m = re.search(
            r"([A-Z][A-Za-z0-9&\-\.]*(?:\s+[A-Za-z0-9&\-\.]+){0,5}?\s+" + COMPANY_SUFFIX + r"\.?)",
            t,
        )
        if m:
            vendor = _clean_vendor(m.group(1))

    if not vendor:
        vendor = "Unknown Vendor"

    return {"vendor": vendor, "amount": amount, "currency": currency, "date": date}


# ---------------------------------------------------------------------------
# Routes
# ---------------------------------------------------------------------------
@app.post("/extract", response_model=InvoiceFields)
def extract(req: ExtractRequest) -> InvoiceFields:
    text = req.text

    llm_out = _extract_with_llm(text)
    det_out = _extract_deterministic(text)

    merged = {
        "vendor": (llm_out or {}).get("vendor") or det_out["vendor"],
        "amount": (llm_out or {}).get("amount", det_out["amount"]),
        "currency": (llm_out or {}).get("currency") or det_out["currency"],
        "date": (llm_out or {}).get("date") or det_out["date"],
    }
    merged["vendor"] = _clean_vendor(str(merged["vendor"]))

    try:
        return InvoiceFields(**merged)
    except Exception:
        return InvoiceFields(**det_out)


@app.get("/health")
def health():
    return {"status": "ok", "llm": _llm is not None}
