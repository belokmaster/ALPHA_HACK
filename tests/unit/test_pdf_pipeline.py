"""PDF regression tests use controlled OCR data; real corpus runs are separate."""

import hashlib
import json
from pathlib import Path

import pytest

from app.ingestion.pdf.pipeline import (
    detect_value_kind,
    extract_pdf_documents_and_evidence,
    load_corpus_config,
    resolve_pdf_metadata,
    safe_parse_ocr_confidence,
)
from contracts.v1 import SourceRef, SourceVersion


@pytest.mark.parametrize("value", [None, "nan", "inf", "bad", -1])
def test_bad_confidence_is_not_a_verified_word(value):
    assert safe_parse_ocr_confidence(value) < 0


def test_supplied_corpus_is_nine_files_without_fabricated_availability():
    c = load_corpus_config()
    assert len(c["documents"]) == 9
    assert all(e["available_at"] is None for e in c["documents"].values())
    assert not c["verified_spans"]  # independent human review is pending, not fabricated
    assert detect_value_kind("Прогноз прошлого года оправдался, но ожидаем спад") == "unknown"


def test_task_or_unknown_pdf_cannot_enter_bank_corpus():
    with pytest.raises(ValueError, match="Unauthorized"):
        resolve_pdf_metadata(Path("Альфа Хак_Новостная аналитика.pdf"), load_corpus_config())


def fixture(tmp_path):
    p = tmp_path / "sample.pdf"
    p.write_bytes(b"controlled PDF bytes; renderer mocked")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    cfg = {
        "documents": {
            p.name: {
                "doc_id": "doc_sample",
                "sha256": sha,
                "pages": 1,
                "title": "Sample",
                "published_at": None,
                "available_at": None,
                "topic": "test",
            }
        },
        "verified_spans": [],
    }
    config = tmp_path / "config.json"
    config.write_text(json.dumps(cfg))
    sv = SourceVersion(
        id="source_sample",
        domain="case",
        ref=SourceRef(source_version_id="source_sample", original_filename=p.name, sha256=sha),
    )
    return p, config, sv


def test_missing_file_is_never_a_verified_span_fixture(tmp_path):
    p, c, s = fixture(tmp_path)
    p.unlink()
    with pytest.raises(FileNotFoundError):
        extract_pdf_documents_and_evidence(p, s, config_path=c)


def test_full_long_page_is_addressable_and_changed_bytes_get_new_version(tmp_path, monkeypatch):
    p, c, s = fixture(tmp_path)
    text = "Число 1.2 единицы USD млн горизонт 2027П; условие.\n" * 180
    monkeypatch.setattr(
        "app.ingestion.pdf.pipeline.extract_pages", lambda *a: [{"page": 1, "text": text}]
    )
    doc, ev, th = extract_pdf_documents_and_evidence(p, s, config_path=c)
    assert "".join(e.quote for e in ev) == text and len(ev) > 3 and not th
    for e in ev:
        _, start, end = e.span.split(":")
        assert text[int(start) : int(end)] == e.quote
        assert e.value_kind == "unknown" and e.quality.value == "unverified"
    again = extract_pdf_documents_and_evidence(p, s, config_path=c)
    assert doc == again[0] and ev == again[1]
    p.write_bytes(b"changed bytes")
    with pytest.raises(ValueError, match="integrity"):
        extract_pdf_documents_and_evidence(p, s, config_path=c)


def test_ocr_failure_does_not_publish_verified_only_fallback(tmp_path, monkeypatch):
    p, c, s = fixture(tmp_path)

    def failed(*a):
        raise RuntimeError("OCR dependency missing")

    monkeypatch.setattr("app.ingestion.pdf.pipeline.extract_pages", failed)
    with pytest.raises(RuntimeError, match="dependency"):
        extract_pdf_documents_and_evidence(p, s, config_path=c)


def test_changed_bytes_with_new_manifest_get_new_immutable_ids(tmp_path, monkeypatch):
    p, c, sv = fixture(tmp_path)
    monkeypatch.setattr(
        "app.ingestion.pdf.pipeline.extract_pages",
        lambda *a: [{"page": 1, "text": "Исходная строка; значение и период неизвестны."}],
    )
    original = extract_pdf_documents_and_evidence(p, sv, config_path=c)
    p.write_bytes(b"new controlled PDF bytes")
    sha = hashlib.sha256(p.read_bytes()).hexdigest()
    config = json.loads(c.read_text())
    config["documents"][p.name]["sha256"] = sha
    c.write_text(json.dumps(config))
    new = sv.model_copy(
        update={
            "id": "source_changed",
            "ref": sv.ref.model_copy(update={"source_version_id": "source_changed", "sha256": sha}),
        }
    )
    changed = extract_pdf_documents_and_evidence(p, new, config_path=c)
    assert original[0].id != changed[0].id and original[1][0].id != changed[1][0].id
    assert original[0].source_version_id == "source_sample"


def test_supersession_matches_metric_subject_and_horizon_and_preserves_old_facts():
    from datetime import UTC, datetime

    from app.ingestion.pdf.pipeline import resolve_thesis_status

    config = {
        "documents": {"old.pdf": {}, "new.pdf": {"published_at": "2026-09-11T00:00:00Z"}},
        "forecast_revisions": [
            {
                "previous_filename": "old.pdf",
                "replacement_filename": "new.pdf",
                "metric": "rate",
                "subject": "CBR",
                "horizon": "end_2026",
            }
        ],
    }
    span = {
        "filename": "old.pdf",
        "metric": "rate",
        "subject": "CBR",
        "horizon": "end_2026",
        "review_status": "human_verified",
    }
    assert resolve_thesis_status(span, config, datetime(2026, 9, 10, tzinfo=UTC)) == "applicable"
    assert resolve_thesis_status(span, config, datetime(2026, 9, 12, tzinfo=UTC)) == "historical"
    assert resolve_thesis_status({**span, "horizon": "end_2027"}, config) == "applicable"
    assert resolve_thesis_status({**span, "metric": "GDP_fact"}, config) == "applicable"
    assert (
        resolve_thesis_status({**span, "review_status": "agent_page_checked"}, config)
        == "historical"
    )
