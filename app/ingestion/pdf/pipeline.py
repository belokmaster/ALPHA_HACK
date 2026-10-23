"""Offline PDF extraction. OCR is data; only explicitly reviewed spans are theses."""

import hashlib
import json
import math
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

from contracts.v1 import BankThesis, Document, Evidence, Quality, SourceVersion

DEFAULT_CORPUS_CONFIG = Path(__file__).resolve().parents[3] / "config/bank_corpus.json"
PARSER_VERSION = "pdf-ocr-2"


def load_corpus_config(config_path: Path = DEFAULT_CORPUS_CONFIG) -> dict:
    return json.loads(config_path.read_text(encoding="utf-8"))


def config_digest(config: dict) -> str:
    return hashlib.sha256(
        json.dumps(
            {"parser": PARSER_VERSION, "config": config}, sort_keys=True, ensure_ascii=False
        ).encode()
    ).hexdigest()


def document_id(entry: dict, config: dict) -> str:
    return f"{entry['doc_id']}_{entry['sha256'][:12]}_{config_digest(config)[:12]}"


@dataclass(frozen=True)
class ResolvedPDFMeta:
    doc_id: str
    published_at: datetime | None
    available_at: datetime | None
    data_as_of: datetime | None
    topic: str
    is_historical: bool
    forecast_horizon: str | None = None


def _infer_date_from_filename(filename):
    # A filename alone does not prove publication, observation or data cutoff.
    return None


def resolve_pdf_metadata(pdf_path: Path, config: dict, allow_dynamic=False) -> ResolvedPDFMeta:
    if pdf_path.name not in config["documents"]:
        raise ValueError("Unauthorized PDF: not in approved bank corpus manifest")
    entry = config["documents"][pdf_path.name]

    def date(name):
        value = entry.get(name)
        if not value:
            return None
        parsed = datetime.fromisoformat(value)
        if parsed.tzinfo is None:
            raise ValueError("Timezone required in corpus dates")
        return parsed.astimezone(UTC)

    if entry.get("available_at") and not entry.get("availability_evidence"):
        raise ValueError("available_at requires independent availability_evidence")
    return ResolvedPDFMeta(
        document_id(entry, config),
        date("published_at"),
        date("available_at"),
        date("data_as_of"),
        entry["topic"],
        entry.get("is_historical", False),
        entry.get("forecast_horizon"),
    )


def safe_parse_ocr_confidence(val):
    try:
        number = float(val)
        return number if math.isfinite(number) else -1.0
    except (ValueError, TypeError):
        return -1.0


def detect_value_kind(text):
    # A mixed OCR paragraph cannot be classified as fact by keyword absence.
    return "unknown"


def is_disclaimer_page(text, page_num, total_pages):
    return page_num == total_pages and (
        "инвестиционной рекомендацией" in text.lower() or "ответственности за" in text.lower()
    )


def resolve_thesis_status(span, config, as_of=None):
    """Span-level supersession; never retire all facts in an older document."""
    entry = config["documents"][span["filename"]]
    if entry.get("is_historical"):
        return "historical"
    for revision in config.get("forecast_revisions", []):
        same = span["filename"] == revision["previous_filename"] and all(
            span.get(k) == revision.get(k) and span.get(k) is not None
            for k in ("metric", "subject", "horizon")
        )
        replacement = config["documents"][revision["replacement_filename"]]
        issued = replacement.get("published_at")
        if same and issued and (as_of is None or datetime.fromisoformat(issued) <= as_of):
            return "historical"
    return "applicable" if span.get("review_status") == "human_verified" else "unverified"


def save_bank_theses(theses, out_path):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(
        json.dumps([t.model_dump(mode="json") for t in theses], ensure_ascii=False, indent=2) + "\n"
    )


def load_bank_theses(in_path):
    if not in_path.exists():
        return []
    return [BankThesis.model_validate(t) for t in json.loads(in_path.read_text())]


def extract_pages(pdf_path: Path, dpi: int, cache_dir: Path | None = None) -> list[dict]:
    import pypdfium2 as pdfium
    import pytesseract

    if not 250 <= dpi <= 300:
        raise ValueError("OCR DPI must be 250..300")
    sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()
    # Runtime versions are part of the cache identity; cached OCR is never silently reused
    # after renderer/engine/traineddata changes.
    import importlib.metadata as md

    runtime = {
        "renderer": md.version("pypdfium2"),
        "pytesseract": md.version("pytesseract"),
        "tesseract": str(pytesseract.get_tesseract_version()),
        "dpi": dpi,
        "languages": "rus+eng",
        "parser": PARSER_VERSION,
    }
    traineddata = {}
    import os
    import subprocess

    prefix = os.environ.get("TESSDATA_PREFIX")
    if not prefix:
        # Explicit runtime preflight: both languages must exist; no partial-page fallback.
        langs = pytesseract.get_languages(config="")
        if not {"rus", "eng"} <= set(langs):
            raise RuntimeError("Tesseract rus+eng traineddata required")
        listing = subprocess.check_output(
            ["tesseract", "--list-langs"], text=True, stderr=subprocess.STDOUT
        )
        prefix = listing.split('"')[1] if '"' in listing else "/usr/share/tesseract-ocr/5/tessdata"
    for language in ("rus", "eng"):
        traineddata[language] = hashlib.sha256(
            (Path(prefix) / (language + ".traineddata")).read_bytes()
        ).hexdigest()
    runtime["traineddata"] = traineddata
    key = hashlib.sha256(json.dumps(runtime, sort_keys=True).encode()).hexdigest()[:16]
    cache = cache_dir / f"{sha}-{key}.json" if cache_dir else None
    if cache and cache.exists():
        return json.loads(cache.read_text())["pages"]
    pages = []
    pdf = pdfium.PdfDocument(str(pdf_path))
    try:
        for i in range(len(pdf)):
            page = pdf[i]
            bitmap = page.render(scale=dpi / 72)
            image = bitmap.to_pil()
            try:
                ocr = pytesseract.image_to_data(
                    image,
                    lang="rus+eng",
                    config="--psm 3",
                    output_type=pytesseract.Output.DICT,
                    timeout=120,
                )
                # Keep line boundaries and word coordinates. Low confidence is retained
                # and flagged, not silently dropped from financial numbers/negations.
                lines = {}
                for j, word in enumerate(ocr["text"]):
                    if not word.strip():
                        continue
                    line = (ocr["block_num"][j], ocr["par_num"][j], ocr["line_num"][j])
                    lines.setdefault(line, []).append(
                        {
                            "text": word.strip(),
                            "confidence": safe_parse_ocr_confidence(ocr["conf"][j]),
                            "bbox": [
                                ocr["left"][j],
                                ocr["top"][j],
                                ocr["width"][j],
                                ocr["height"][j],
                            ],
                        }
                    )
                text = "\n".join(" ".join(w["text"] for w in words) for words in lines.values())
                if not text.strip():
                    raise ValueError(f"Empty OCR page {i + 1}: {pdf_path.name}")
                pages.append(
                    {"page": i + 1, "text": text, "lines": list(lines.values()), "dpi": dpi}
                )
            finally:
                image.close()
                bitmap.close()
                page.close()
    finally:
        pdf.close()
    if cache:
        cache.parent.mkdir(parents=True, exist_ok=True)
        cache.write_text(
            json.dumps({"sha256": sha, "runtime": runtime, "pages": pages}, ensure_ascii=False)
        )
    return pages


def extract_pdf_documents_and_evidence(
    pdf_path: Path,
    source_version: SourceVersion,
    dpi=300,
    config_path=DEFAULT_CORPUS_CONFIG,
    allow_dynamic=False,
    *,
    cache_dir=None,
    corpus_config=None,
):
    config = corpus_config if corpus_config is not None else load_corpus_config(config_path)
    meta = resolve_pdf_metadata(pdf_path, config)
    entry = config["documents"][pdf_path.name]
    actual_sha = hashlib.sha256(pdf_path.read_bytes()).hexdigest()  # missing input must fail
    if actual_sha != entry["sha256"] or actual_sha != source_version.ref.sha256:
        raise ValueError("PDF integrity violation: sha256 mismatch")
    if source_version.available_at != meta.available_at:
        raise ValueError("Source/document availability mismatch")
    pages = extract_pages(pdf_path, dpi, cache_dir)
    if len(pages) != entry["pages"]:
        raise ValueError("PDF page count differs from corpus manifest")
    evidences = []
    theses = []
    full = []
    for page in pages:
        text = page["text"]
        full.append(f"[Стр. {page['page']}]\n{text}")
        if is_disclaimer_page(text, page["page"], len(pages)):
            continue
        # Never truncate a block at 1800 chars. Every char of the OCR page is addressable.
        # Layout/table integrity is preserved in the page/word cache; raw chunks are
        # unknown/unverified and cannot support a numeric bank thesis automatically.
        start = 0
        while start < len(text):
            end = min(start + 1600, len(text))
            if end < len(text):
                boundary = text.rfind("\n", start + 800, end)
                if boundary >= 0:
                    end = boundary + 1
            evidences.append(
                Evidence(
                    id=f"ev_{meta.doc_id}_p{page['page']}_{start}_{end}",
                    document_version_id=meta.doc_id,
                    source_version_id=source_version.id,
                    quote=text[start:end],
                    page=page["page"],
                    span=f"page_chars:{start}:{end}",
                    value_kind="unknown",
                    quality=Quality.unverified,
                    domain=source_version.domain,
                    available_at=meta.available_at,
                )
            )
            start = end
    for span in config.get("verified_spans", []) + config.get("agent_spans", []):
        if span["filename"] != pdf_path.name:
            continue
        # Review provenance must be explicit; an agent's OCR check is not a human review.
        human = span.get("review_status") == "human_verified"
        agent = span.get("review_status") == "agent_page_checked"
        if not (human or agent) or not span.get("reviewer"):
            continue
        page = pages[span["page"] - 1]
        start, end = span["start"], span["end"]
        if page["text"][start:end] != span["quote"] or span["sha256"] != actual_sha:
            raise ValueError("Reviewed span does not match source page transcription")
        eid = f"ev_{meta.doc_id}_{span['span']}"
        evidences.append(
            Evidence(
                id=eid,
                document_version_id=meta.doc_id,
                source_version_id=source_version.id,
                quote=span["quote"],
                page=span["page"],
                span=f"page_chars:{start}:{end}",
                value_kind=span["value_kind"],
                quality=Quality.verified if human else Quality.unverified,
                domain=source_version.domain,
                available_at=meta.available_at,
            )
        )
        if human:
            theses.append(
                BankThesis(
                    id=f"th_{eid}",
                    evidence_ids=[eid],
                    text=span["quote"],
                    issued_at=meta.published_at,
                    horizon=span.get("horizon"),
                    status=resolve_thesis_status(span, config),
                )
            )
    document = Document(
        id=meta.doc_id,
        source_version_id=source_version.id,
        domain=source_version.domain,
        title=entry["title"],
        publisher="Alfa-Research",
        published_at=meta.published_at,
        available_at=meta.available_at,
        text="\n\n".join(full),
    )
    return document, evidences, theses
