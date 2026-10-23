"""Review tooling tests use synthetic DTOs; they are not human G1 decisions."""

import copy
import json
from pathlib import Path

import pytest

from app.ingestion.news.parse import Tree
from contracts.examples import examples
from contracts.v1 import Analysis
from scripts import review_g1 as review


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False))


@pytest.fixture
def saved_pair():
    base = next(x for x in examples() if isinstance(x, Analysis)).model_dump(mode="json")
    ev = base["evidence"][0]
    ev["page"] = 1
    ev["span"] = f"page_chars:0:{len(ev['quote'])}"
    follow = copy.deepcopy(base)
    follow.update(id="follow", parent_analysis_id=base["id"])
    snapshot = {
        "stored_analyses": copy.deepcopy([base, follow]),
        "evidence": copy.deepcopy(base["evidence"]),
        "sources": [{"id": ev["source_version_id"]}],
        "documents": [
            {
                "id": ev["document_version_id"],
                "source_version_id": ev["source_version_id"],
                "text": f"[Стр. 1]\n{ev['quote']}",
            }
        ],
    }
    return base, follow, snapshot


def test_snapshot_rejects_another_parent_and_context(saved_pair):
    base, follow, snapshot = saved_pair
    review.validate_snapshot(base, follow, snapshot)
    follow["parent_analysis_id"] = "another-run"
    with pytest.raises(ValueError, match="another analysis"):
        review.validate_snapshot(base, follow, snapshot)
    follow["parent_analysis_id"] = base["id"]
    follow["context"]["version"] += 1
    with pytest.raises(ValueError, match="context differs"):
        review.validate_snapshot(base, follow, snapshot)


def test_snapshot_rejects_changed_output_or_quote(saved_pair):
    base, follow, snapshot = saved_pair
    base["claims"][0]["text"] += " Changed output"
    with pytest.raises(ValueError, match="Saved result differs"):
        review.validate_snapshot(base, follow, snapshot)
    base["claims"][0]["text"] = snapshot["stored_analyses"][0]["claims"][0]["text"]
    snapshot["documents"][0]["text"] = "[Стр. 1]\n" + "x" * 100
    with pytest.raises(ValueError, match="Quote/span mismatch"):
        review.validate_snapshot(base, follow, snapshot)


def test_missing_pdf_reports_exact_original_not_ocr(tmp_path):
    ocr = tmp_path / "cache.json"
    ocr.write_text('{"text":"Original PDF is not present"}')
    lock = {
        "files": {},
        "pdfs": {"sv": {"path": "raw/pdf/missing.pdf", "sha256": "a" * 64}},
        "ocr": {"sv": {"path": "cache.json", "sha256": review.sha(ocr.read_bytes())}},
        "company_csv": {"path": "cache.json", "sha256": review.sha(ocr.read_bytes())},
        "news": {},
    }
    with pytest.raises(ValueError) as error:
        review.check_inputs(lock, tmp_path)
    message = json.loads(str(error.value))
    assert message["required_files"][0]["path"] == "raw/pdf/missing.pdf"
    assert message["ocr_substitution_allowed"] is False


def test_word_boxes_respect_lines_and_half_open_span():
    page = {
        "text": "one two\nthree",
        "lines": [
            [{"text": "one", "bbox": [1, 2, 3, 4]}, {"text": "two", "bbox": [8, 2, 4, 4]}],
            [{"text": "three", "bbox": [1, 10, 7, 4]}],
        ],
    }
    assert review.word_boxes(page, 4, 7) == [[8, 2, 12, 6]]
    assert review.word_boxes(page, 8, 13) == [[1, 10, 8, 14]]
    page["text"] = "different OCR version"
    with pytest.raises(ValueError, match="coordinate mismatch"):
        review.word_boxes(page, 0, 3)


def test_news_visual_keeps_header_cell_order_and_safe_escaping(tmp_path):
    tree = Tree()
    tree.feed(
        '<table><tr><th rowspan="2">First</th><td>Second &amp; &lt;x&gt;</td><th>Third</th></tr></table>'
    )
    row = next(n for n in tree.nodes if n.tag == "tr")
    assert [n.tag for n in review.html_cells(row)] == ["th", "td", "th"]
    svg = tmp_path / "safe.svg"
    review.svg_text(svg, "fixture", ['<script>alert("data")</script>'])
    assert "<script>" not in svg.read_text()
    assert "&lt;script&gt;" in svg.read_text()


@pytest.fixture
def packet(tmp_path):
    # Five synthetic claims only exercise whole-result coverage invariants.
    base = {
        "id": "base",
        "claims": [{"id": "a", "text": "Synthetic a"}],
        "evidence": [{"id": "ev"}],
    }
    follow = {
        "id": "follow",
        "claims": [{"id": str(i), "text": f"Synthetic {i}"} for i in range(4)],
        "evidence": [{"id": "ev"}],
    }
    rows = [
        review.record(
            f"{a['id']}:{c['id']}",
            "fixture",
            c["text"],
            "fixture question",
            claim_id=c["id"],
            analysis_id=a["id"],
        )
        for a in (base, follow)
        for c in a["claims"]
    ]
    rows.append(
        review.record("evidence:ev", "fixture", "quote", "fixture question", evidence_id="ev")
    )
    write_json(tmp_path / "frozen/base.json", base)
    write_json(tmp_path / "frozen/followup.json", follow)
    write_json(tmp_path / "review-rows.json", rows)
    answers = {
        "packet_id": "synthetic-packet",
        "reviewer": "",
        "reviewed_at": "",
        "rows": [{"id": r["id"], "decision": "", "comment": ""} for r in rows],
    }
    write_json(tmp_path / "decisions-template.json", answers)
    (tmp_path / "index.html").write_text(
        '<p id="source"><a href="frozen/base.json">Source</a><a href="#source">Up</a></p>'
    )
    write_manifest(tmp_path)
    return tmp_path


def write_manifest(path):
    write_json(
        path / "manifest.json",
        {
            "packet_id": "synthetic-packet",
            "files": {
                str(p.relative_to(path)): review.sha(p.read_bytes())
                for p in path.rglob("*")
                if p.is_file() and p.name != "manifest.json"
            },
        },
    )


def test_verifier_detects_incomplete_claim_coverage_even_if_hashes_updated(packet):
    rows = review.read_json(packet / "review-rows.json")
    rows[0]["exact_text"] = "another text"
    write_json(packet / "review-rows.json", rows)
    write_manifest(packet)
    with pytest.raises(AssertionError):
        review.verify_packet(packet)


def test_decisions_remain_separate_and_require_correct_packet_and_coverage(packet, tmp_path):
    answer = tmp_path.parent / (tmp_path.name + "-synthetic-decisions.json")
    original = (packet / "review-rows.json").read_bytes()
    payload = review.read_json(packet / "decisions-template.json")
    write_json(answer, payload)
    assert review.validate_decisions(packet, answer)["answered"] == 0
    with pytest.raises(ValueError, match="incomplete"):
        review.validate_decisions(packet, answer, complete=True)
    payload["packet_id"] = "wrong-version"
    write_json(answer, payload)
    with pytest.raises(ValueError, match="another packet"):
        review.validate_decisions(packet, answer)
    payload["packet_id"] = "synthetic-packet"
    payload["rows"][1] = payload["rows"][0]
    write_json(answer, payload)
    with pytest.raises(ValueError, match="coverage"):
        review.validate_decisions(packet, answer)
    assert (packet / "review-rows.json").read_bytes() == original


def test_human_decision_needs_identity_and_does_not_write_evidence(packet, tmp_path):
    answer = tmp_path.parent / (tmp_path.name + "-synthetic-decisions.json")
    payload = review.read_json(packet / "decisions-template.json")
    payload["rows"][0]["decision"] = "недостаточно данных"
    write_json(answer, payload)
    with pytest.raises(ValueError, match="Reviewer and date"):
        review.validate_decisions(packet, answer)
    payload.update(reviewer="Synthetic unit test only", reviewed_at="2026-10-22")
    write_json(answer, payload)
    result = review.validate_decisions(packet, answer)
    assert result["answered"] == 1 and result["evidence_modified"] is False
    assert review.read_json(packet / "decisions-template.json")["rows"][0]["decision"] == ""


def test_integrity_and_local_links_checked(packet):
    assert review.verify_links(packet) == 2
    (packet / "index.html").write_text('<a href="../outside.pdf">broken</a>')
    with pytest.raises(ValueError, match="changed"):
        review.verify_packet(packet)
    with pytest.raises(ValueError, match="inside the repository"):
        review.verify_links(packet)


def test_input_path_cannot_escape_repository(tmp_path):
    with pytest.raises(ValueError, match="inside the repository"):
        review.locked_path({"path": "../outside.pdf"}, tmp_path)


def test_existing_review_output_is_never_overwritten(tmp_path, monkeypatch):
    monkeypatch.setattr(review, "LOCK", Path("config/g1-review.json"))
    monkeypatch.setattr(review, "check_inputs", lambda lock: None)
    with pytest.raises(ValueError, match="Output exists"):
        review.build(tmp_path)
