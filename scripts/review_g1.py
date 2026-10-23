"""Frozen whole-result review packet. No imports, generation, retrieval or model calls.

All inputs are SHA-pinned. Original PDF bytes are required, never replaced by OCR.
OCR word boxes locate crops only; human decisions remain a separate unsigned file.
"""

import argparse
import base64
import csv
import hashlib
import html
import json
import math
import os
import shutil
import tempfile
import textwrap
import zipfile
from html.parser import HTMLParser
from pathlib import Path
from urllib.parse import unquote, urlsplit

from jinja2 import Environment, FileSystemLoader, select_autoescape

from contracts.v1 import Analysis

ROOT = Path(__file__).resolve().parents[1]
LOCK = ROOT / "config/g1-review.json"
DECISIONS = {"", "подтверждено", "ошибка", "недостаточно данных"}


def sha(data):
    return hashlib.sha256(data).hexdigest()


def read_json(path):
    return json.loads(path.read_text())


def locked_path(entry, root=ROOT):
    path = (root / entry["path"]).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError("Review inputs must stay inside the repository")
    return path


def check_inputs(lock, root=ROOT):
    entries = (
        list(lock["files"].values()) + list(lock["pdfs"].values()) + list(lock["ocr"].values())
    )
    entries += [lock["company_csv"]] + [
        e for group in lock["news"].values() for e in group.values()
    ]
    errors = []
    for entry in entries:
        path = locked_path(entry, root)
        if not path.is_file():
            errors.append(
                {
                    "path": entry["path"],
                    "required_sha256": entry["sha256"],
                    "error": "missing original input",
                }
            )
        elif sha(path.read_bytes()) != entry["sha256"]:
            errors.append(
                {
                    "path": entry["path"],
                    "required_sha256": entry["sha256"],
                    "error": "version/hash differs",
                }
            )
    if errors:
        raise ValueError(
            json.dumps(
                {"required_files": errors, "ocr_substitution_allowed": False},
                ensure_ascii=False,
                indent=2,
            )
        )


def validate_snapshot(analysis, follow, snapshot):
    Analysis.model_validate(analysis)
    Analysis.model_validate(follow)
    if follow["parent_analysis_id"] != analysis["id"]:
        raise ValueError("Follow-up belongs to another analysis")
    for key in (
        "company_id",
        "company_snapshot_id",
        "event_id",
        "context",
        "temporal",
        "policy_version",
    ):
        if analysis[key] != follow[key]:
            raise ValueError("Parent/follow-up pinned context differs: " + key)
    evidence = {e["id"]: e for e in snapshot["evidence"]}
    documents = {d["id"]: d for d in snapshot["documents"]}
    sources = {s["id"]: s for s in snapshot["sources"]}
    for result in (analysis, follow):
        stored = next(a for a in snapshot["stored_analyses"] if a["id"] == result["id"])
        if {k: v for k, v in stored.items() if k != "delivery"} != {
            k: v for k, v in result.items() if k != "delivery"
        }:
            raise ValueError("Saved result differs from its database snapshot")
        for ev in result["evidence"]:
            if evidence.get(ev["id"]) != ev:
                raise ValueError("Evidence version collision: " + ev["id"])
            doc = documents[ev["document_version_id"]]
            assert sources[ev["source_version_id"]]["id"] == doc["source_version_id"]
            _, start, end = ev["span"].split(":")
            if ev["page"]:
                text = doc["text"].split(f"[Стр. {ev['page']}]\n", 1)[1].split("\n\n[Стр.", 1)[0]
            else:
                text = doc["text"]
            if text[int(start) : int(end)] != ev["quote"]:
                raise ValueError("Quote/span mismatch: " + ev["id"])
    return documents, sources


def word_boxes(page, start, end):
    """Locate an exact stored page_chars interval in the old OCR coordinate system."""
    reconstructed = "\n".join(" ".join(w["text"] for w in line) for line in page["lines"])
    if reconstructed != page["text"] or not 0 <= start < end <= len(reconstructed):
        raise ValueError("OCR cache/character coordinate mismatch")
    cursor, boxes = 0, []
    for line in page["lines"]:
        for word in line:
            stop = cursor + len(word["text"])
            if cursor < end and stop > start:
                x, y, w, h = word["bbox"]
                boxes.append([x, y, x + w, y + h])
            cursor = stop + 1
    if not boxes:
        raise ValueError("No OCR coordinates for quote")
    return boxes


def numbers(text):
    import re

    return sorted(set(re.findall(r"[-+]?\d+(?:[,.]\d+)?(?:\s*%|\s*г\.|\s*млрд)?", text)))


def html_cells(node):
    """Keep mixed th/td cells in original DOM order, including merged-cell metadata."""
    result = []
    for child in node.children:
        if not hasattr(child, "tag"):
            continue
        if child.tag in {"td", "th"}:
            result.append(child)
        else:
            result.extend(html_cells(child))
    return result


def svg_text(path, heading, lines, width=1200):
    """Safe derived text visual, not a screenshot or an OCR substitute for a PDF."""
    wrapped = []
    for line in [heading, *lines]:
        wrapped.extend(textwrap.wrap(line, width=105, replace_whitespace=False) or [""])
    height = 60 + len(wrapped) * 25
    body = "".join(
        f'<text x="20" y="{40 + i * 25}" font-size="18">{html.escape(line)}</text>'
        for i, line in enumerate(wrapped)
    )
    path.write_text(
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{width}" height="{height}" viewBox="0 0 {width} {height}"><rect width="100%" height="100%" fill="white"/><g font-family="sans-serif" fill="#171717">{body}</g></svg>'
    )


def render_pdfs(out, lock, snapshot, analyses):
    import pypdfium2 as pdfium
    from PIL import ImageDraw

    assets, pages, crops = {}, {}, {}
    wanted = {e["id"]: e for a in analyses for e in a["evidence"] if e["page"]}
    for sid, entry in lock["pdfs"].items():
        original = locked_path(entry)
        dest = out / "originals/pdf" / original.name
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(original, dest)
        assets[sid] = str(dest.relative_to(out))
        cache = read_json(locked_path(lock["ocr"][sid]))
        assert cache["sha256"] == entry["sha256"]
        doc = pdfium.PdfDocument(str(original))
        for index in range(len(doc)):
            page = doc[index]
            bitmap = page.render(scale=2)
            image = bitmap.to_pil()
            stem = f"pdf-{entry['sha256'][:12]}-p{index + 1}"
            full = out / "images" / (stem + ".png")
            image.save(full)
            ocr = cache["pages"][index]
            assert ocr["page"] == index + 1
            pages[(sid, index + 1)] = {
                "image": str(full.relative_to(out)),
                "size_points": list(page.get_size()),
                "ocr": ocr,
                "dpi": cache["runtime"]["dpi"],
            }
            for ev in wanted.values():
                if ev["source_version_id"] != sid or ev["page"] != index + 1:
                    continue
                _, start, end = ev["span"].split(":")
                start, end = int(start), int(end)
                assert ocr["text"][start:end] == ev["quote"]
                boxes = word_boxes(ocr, start, end)
                factor = 144 / cache["runtime"]["dpi"]
                x0, y0 = [
                    max(0, math.floor(min(b[i] for b in boxes) * factor) - 18) for i in (0, 1)
                ]
                x1 = min(image.width, math.ceil(max(b[2] for b in boxes) * factor) + 18)
                y1 = min(image.height, math.ceil(max(b[3] for b in boxes) * factor) + 18)
                crop = image.crop((x0, y0, x1, y1))
                draw = ImageDraw.Draw(crop)
                for box in boxes:
                    draw.rectangle(
                        [
                            box[0] * factor - x0,
                            box[1] * factor - y0,
                            box[2] * factor - x0,
                            box[3] * factor - y0,
                        ],
                        outline="#d93025",
                        width=1,
                    )
                name = "quote-" + sha(ev["id"].encode())[:14] + ".png"
                crop.save(out / "images" / name)
                crop.close()
                bbox = [
                    min(b[0] for b in boxes),
                    min(b[1] for b in boxes),
                    max(b[2] for b in boxes),
                    max(b[3] for b in boxes),
                ]
                crops[ev["id"]] = {
                    "image": "images/" + name,
                    "full_page": str(full.relative_to(out)),
                    "page": index + 1,
                    "span": ev["span"],
                    "bbox_ocr_pixels": bbox,
                    "ocr_dpi": cache["runtime"]["dpi"],
                    "bbox_pdf_points_top_left": [
                        round(v * 72 / cache["runtime"]["dpi"], 2) for v in bbox
                    ],
                    "note": "Красные рамки — координаты старого OCR, не human_verified; оригинальная страница без рамок рядом.",
                }
            image.close()
            bitmap.close()
            page.close()
        doc.close()
    return assets, pages, crops


def record(
    row_id,
    section,
    text,
    question,
    *,
    claim_id=None,
    analysis_id=None,
    kind="not a claim",
    **kwargs,
):
    return dict(
        id=row_id,
        group=section,
        claim_id=claim_id,
        analysis_id=analysis_id,
        exact_text=text,
        type=kind,
        question=question,
        decision="",
        comment="",
        **kwargs,
    )


def make_rows(analyses, documents, sources, crops, assets):
    rows = []

    def reference(ev):
        return {
            **ev,
            "source_file": assets[ev["source_version_id"]],
            "published_at": documents[ev["document_version_id"]]["published_at"],
            **crops[ev["id"]],
        }

    for label, a in zip(("base", "followup"), analyses, strict=True):
        evs = {e["id"]: e for e in a["evidence"]}
        for c in a["claims"]:
            group = (
                "Пользовательские допущения"
                if c["origin"] == "user_declared"
                else (
                    "Выводы о компании"
                    if c["kind"] in {"hypothesis", "ai_inference"}
                    else "Утверждения анализа"
                )
            )
            refs = [reference(evs[eid]) for eid in c["evidence_ids"]]
            gap = []
            if c["origin"] == "model":
                gap = [
                    "Цитата о ключевой ставке и флаг LOA сами по себе не доказывают тип договора, будущую потребность и знак/размер эффекта.",
                    "Прогнозы 13,5→14 / 12% находятся в фоне, но этот claim ссылается только на event_0 и LOA. Оцените достаточность именно заявленных ссылок.",
                ]
            if c["origin"] == "user_declared":
                gap = [
                    "Независимый договор не представлен. Это сообщение пользователя, не факт банка."
                ]
            question = (
                "Поддерживают ли событие и наблюдаемый LOA условный переход к этому выводу для U_15728? "
                "Не выданы ли неизвестные условия за факт, и не потеряны ли альтернативы?"
                if c["origin"] == "model"
                else (
                    "Точно ли сохранено пользовательское условие и ограничено ли им только обсуждение действующего договора, а не нового финансирования?"
                    if c["origin"] == "user_declared"
                    else "Совпадает ли цитата с исходной страницей, сохранена ли пауза (не повышение), дата и атрибуция по отчёту банка?"
                )
            )
            rows.append(
                record(
                    label + ":" + c["id"],
                    group,
                    c["text"],
                    question,
                    claim_id=c["id"],
                    analysis_id=a["id"],
                    kind={
                        "fact": "external fact",
                        "bank_view": "bank_view",
                        "ai_inference": "inference",
                        "hypothesis": "hypothesis",
                    }[c["kind"]],
                    origin=c["origin"],
                    refs=refs,
                    feature_ids=c["feature_ids"],
                    conditions=c["conditions"],
                    alternatives=c["alternatives"],
                    numbers=numbers(c["text"]),
                    gaps=gap,
                    pointer=f"frozen/{label}.json#/claims/{a['claims'].index(c)}",
                    message_file="frozen/database.json#/messages"
                    if c["origin"] == "user_declared"
                    else None,
                )
            )
    for ev in analyses[0]["evidence"]:
        metadata = sources[ev["source_version_id"]]
        rows.append(
            record(
                "evidence:" + ev["id"],
                "Прогнозы и фоновые доказательства",
                ev["quote"],
                "Это фон, а не bank_view результата. Сверьте все числа/единицы/горизонты с изображением; оцените применимость к событию, а не только совпадение OCR.",
                refs=[reference(ev)],
                evidence_id=ev["id"],
                source_version=ev["source_version_id"],
                quality=ev["quality"],
                value_kind=ev["value_kind"],
                numbers=numbers(ev["quote"]),
                published_at=documents[ev["document_version_id"]]["published_at"],
                available_at=metadata["available_at"],
                gaps=[
                    "Сырая OCR-цитата: названия строк/колонок и сноски могли быть потеряны. Не переносить числа без исходной страницы."
                ]
                if ev["value_kind"] == "unknown"
                else [],
            )
        )
    return rows


def add_company(out, lock, analyses, rows):
    path = locked_path(lock["company_csv"])
    used = {fid for a in analyses for c in a["claims"] for fid in c["feature_ids"]}
    refs = {
        p["row"]
        for f in analyses[0]["features"]
        for p in f["provenance"]
        if p["original_filename"] == path.name
    }
    found = {}
    with path.open(newline="") as stream:
        for n, row in enumerate(csv.DictReader(stream, delimiter=";"), 2):
            if n in refs:
                found[n] = row
    checked = 0
    for f in analyses[0]["features"]:
        for ref in f["provenance"]:
            if ref["original_filename"] != path.name:
                continue
            assert ref["sha256"] == lock["company_csv"]["sha256"]
            if found[ref["row"]][ref["field"]] != f["raw_value"]:
                raise ValueError("CSV cell/raw feature mismatch: " + f["id"])
            checked += 1
    dest = out / "originals" / path.name
    shutil.copyfile(path, dest)
    for name in ("CL_PRODUCT_LOA_MARK", "CL_PRODUCT_LEA_MARK", "MAINOKVED_CODE"):
        feature = next(f for f in analyses[0]["features"] if f["name"] == name)
        ref = feature["provenance"][0]
        display = [
            f"{k} = {v}"
            for k, v in found[ref["row"]].items()
            if k
            in {
                "SYNTHETIC_U",
                "CL_TYPE",
                "MAINOKVED_CODE",
                "CL_PRODUCT_LOA_MARK",
                "CL_PRODUCT_LEA_MARK",
            }
        ]
        image = "images/company-" + name + ".svg"
        svg_text(out / image, f"Производное изображение CSV строки {ref['row']} (не PDF)", display)
        rows.append(
            record(
                "company:" + feature["id"],
                "Выводы о компании",
                json.dumps(feature, ensure_ascii=False, indent=2),
                "Совпадают ли U, физическая строка, поле и raw value? LOA подтверждает наблюдаемый продукт, но не сумму/тип ставки/дату пересмотра. Не приписаны ли отрасли неподтверждённые расходы?",
                source_version=ref["source_version_id"],
                source_file=str(dest.relative_to(out)),
                page=None,
                span=f"CSV row {ref['row']}, field {ref['field']}",
                feature_ids=[feature["id"]],
                used_in_claim=feature["id"] in used,
                image=image,
                refs=[],
                numbers=numbers(feature["raw_value"] or ""),
                gaps=["Параметры договора и планы финансирования отсутствуют."],
            )
        )
    return checked


def add_page_context(out, rows, pages, assets, analyses):
    # These regions are review aids, not new evidence or a corrected transcription.
    for (sid, page_num), page in pages.items():
        if ("848995" in sid and page_num in (1, 3)) or page_num == max(
            p for s, p in pages if s == sid
        ):
            table = "848995" in sid and page_num in (1, 3)
            rows.append(
                record(
                    f"page:{sid}:{page_num}",
                    "Числовые таблицы и сноски" if table else "Оговорки источника",
                    page["ocr"]["text"],
                    "Сверьте заголовки строк/колонок, знаки, десятичные запятые, г/г против конца периода и звёздочки «Прогноз». Это контекст страницы, не новый claim. Для графика ставки сравните конец 2026/2027 с более поздним отчётом."
                    if table
                    else "Сохранены ли оговорки, источники, ограничения распространения и отсутствие индивидуальной инвестрекомендации?",
                    source_version=sid,
                    source_file=assets[sid],
                    page=page_num,
                    span=f"page_chars:0:{len(page['ocr']['text'])}",
                    image=page["image"],
                    refs=[],
                    numbers=numbers(page["ocr"]["text"]),
                    gaps=[
                        "На Macro p1 OCR не сохранил подписи строк/колонок и сноску «* Прогноз» полностью; не исправлялось в исходном Evidence."
                    ]
                    if table and page_num == 1
                    else [],
                )
            )
    for label, a in zip(("base", "followup"), analyses, strict=True):
        text = json.dumps(
            {
                k: a[k]
                for k in (
                    "sections",
                    "questions",
                    "missing_fields",
                    "notices",
                    "bank_evidence_status",
                    "temporal",
                    "assumptions",
                )
            },
            ensure_ascii=False,
            indent=2,
        )
        rows.append(
            record(
                "completeness:" + a["id"],
                "Полнота анализа",
                text,
                "Проверены ли все шесть разделов, включая пустые bank_view/actions/consequences? Соответствуют ли вопросы, ограничения, неизвестные даты и источник каждого вывода показанным claims?",
                analysis_id=a["id"],
                source_file=f"frozen/{label}.json",
                refs=[],
                numbers=[],
                gaps=[],
            )
        )


def news_context(out, lock, snapshot, rows):
    from app.ingestion.news.parse import Tree

    documents = {d["id"]: d for d in snapshot["documents"]}
    data = []
    for name, files in lock["news"].items():
        receipt = read_json(locked_path(files["json"]))
        doc = documents[receipt["document_id"]]
        assert sha(locked_path(files["html"]).read_bytes()) == receipt["html_sha256"]
        assert sha(doc["text"].encode()) == receipt["text_sha256"]
        refs = [e for e in snapshot["evidence"] if e["document_version_id"] == doc["id"]]
        refs.sort(key=lambda e: int(e["span"].split(":")[1]))
        assert "".join(e["quote"] for e in refs) == doc["text"]
        dest = out / "originals/news" / name
        dest.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(locked_path(files["html"]), dest / "article.html.txt")
        shutil.copyfile(locked_path(files["xml"]), dest / "rss.xml.txt")
        tree = Tree()
        tree.feed(locked_path(files["html"]).read_text())
        tables = [n for n in tree.nodes if n.tag == "table"]
        visuals = []
        original_images = []
        for index, node in enumerate(n for n in tree.nodes if n.tag == "img"):
            src = node.attrs.get("src", "")
            if not src.startswith("data:image/png;base64,"):
                continue  # Never fetch logos/trackers or a changed external image.
            body = base64.b64decode(src.split(",", 1)[1], validate=True)
            if not body.startswith(b"\x89PNG\r\n\x1a\n"):
                raise ValueError("Unexpected embedded PNG bytes")
            dest_image = dest / f"embedded-{index + 1}.png"
            dest_image.write_bytes(body)
            original_images.append(
                {
                    "image": str(dest_image.relative_to(out)),
                    "sha256": sha(body),
                    "span": f"HTML img[{index + 1}] src data:image/png;base64 (DOM order, 1-based)",
                }
            )

        def descendants(node, tag):
            return [
                child
                for c in node.children
                if hasattr(c, "tag")
                for child in ([c] if c.tag == tag else descendants(c, tag))
            ]

        for t_index, table in enumerate(tables):
            all_rows = descendants(table, "tr")
            for offset in range(0, len(all_rows), 20):
                lines = []
                for index, tr in enumerate(all_rows[offset : offset + 20], offset + 1):
                    cells = html_cells(tr)
                    lines.append(
                        f"Строка {index}: "
                        + " | ".join(
                            f"{c.text().strip()} [rowspan={c.attrs.get('rowspan', '1')}, colspan={c.attrs.get('colspan', '1')}]"
                            for c in cells
                        )
                    )
                image = f"images/{name}-table-{t_index + 1}-rows-{offset + 1}.svg"
                svg_text(
                    out / image,
                    f"Визуализация сохранённого HTML: {name}, таблица {t_index + 1}; не скриншот сайта",
                    lines,
                )
                visuals.append(image)
        if not visuals:
            image = f"images/{name}-text.svg"
            svg_text(
                out / image,
                "Производное изображение сохранённого HTML, не скриншот сайта",
                [doc["title"], doc["text"]],
            )
            visuals = [image]
        rows.append(
            record(
                "news:" + doc["id"],
                "Новости отдельно от исторического G1",
                doc["text"],
                "Заголовок, публикация, наблюдение и период в новости различны? Сохранены ли все строки, единицы и сноски? Эта октябрьская статья НЕ является источником сентябрьского G1.",
                source_version=doc["source_version_id"],
                source_file=str((dest / "article.html.txt").relative_to(out)),
                url=doc["url"],
                page=None,
                span="HTML captured document; Evidence " + ", ".join(e["span"] for e in refs),
                refs=refs,
                images=visuals,
                original_images=original_images,
                numbers=numbers(doc["text"]),
                published_at=doc["published_at"],
                available_at=doc["available_at"],
                gaps=[
                    "Нет связи с claims выбранного G1; не использовать как историческое доказательство."
                ]
                + (
                    [
                        "В HTML есть встроенный график: его точные PNG bytes приложены. Document.text/Evidence не содержат чисел этого изображения; не считать полноту таблиц полнотой графика. Человеку нужно сверить оси, легенду и значения с PNG, не с OCR."
                    ]
                    if original_images
                    else []
                ),
            )
        )
        data.append(
            {
                "name": name,
                "document": doc,
                "receipt": receipt,
                "tables": len(tables),
                "visuals": visuals,
            }
        )
    return data


class ReviewLinks(HTMLParser):
    def __init__(self):
        super().__init__()
        self.ids = set()
        self.links = []

    def handle_starttag(self, tag, attrs):
        attrs = dict(attrs)
        if attrs.get("id"):
            self.ids.add(attrs["id"])
        for key in ("href", "src"):
            if attrs.get(key):
                self.links.append(attrs[key])


def verify_links(directory):
    parser = ReviewLinks()
    parser.feed((directory / "index.html").read_text())
    for link in parser.links:
        url = urlsplit(link)
        if url.scheme == "https":
            continue  # URLs identify sources; do not silently replace them by fresh versions.
        if url.scheme or url.netloc:
            raise ValueError("Unexpected external reference: " + link)
        if url.path:
            target = locked_path({"path": unquote(url.path)}, directory)
            if not target.is_file():
                raise ValueError("Missing linked packet file: " + link)
        elif unquote(url.fragment) not in parser.ids:
            raise ValueError("Missing page anchor: " + link)
    return len(parser.links)


def verify_packet(directory):
    manifest = read_json(directory / "manifest.json")
    for name, digest in manifest["files"].items():
        if sha((directory / name).read_bytes()) != digest:
            raise ValueError("Packet file changed: " + name)
    rows = read_json(directory / "review-rows.json")
    template = read_json(directory / "decisions-template.json")
    assert all(r["decision"] == r["comment"] == "" for r in rows)
    assert all(r["decision"] == r["comment"] == "" for r in template["rows"])
    assert len({r["id"] for r in rows}) == len(rows)
    assert {r["id"] for r in rows} == {r["id"] for r in template["rows"]}
    assert len([r for r in rows if r["claim_id"]]) == 5
    base = read_json(directory / "frozen/base.json")
    follow = read_json(directory / "frozen/followup.json")
    assert {(r["analysis_id"], r["claim_id"], r["exact_text"]) for r in rows if r["claim_id"]} == {
        (a["id"], c["id"], c["text"]) for a in (base, follow) for c in a["claims"]
    }
    assert {r["evidence_id"] for r in rows if r.get("evidence_id")} == {
        e["id"] for e in base["evidence"] + follow["evidence"]
    }
    for row in rows:
        for name in [row.get("image"), *row.get("images", [])]:
            if name:
                assert (directory / name).is_file()
        for ref in row.get("refs", []):
            if ref.get("image"):
                assert (directory / ref["image"]).is_file()
    verify_links(directory)
    return manifest


def restore_inputs(directory):
    """Restore pinned local build inputs; never replace an existing different version."""
    verify_packet(directory)
    mapping = read_json(directory / "rebuild-inputs.json")
    entries = []
    for entry in mapping:
        source = locked_path({"path": entry["packet_path"]}, directory)
        destination = locked_path(entry)
        if sha(source.read_bytes()) != entry["sha256"]:
            raise ValueError("Restoration input hash mismatch: " + entry["path"])
        if destination.exists() and sha(destination.read_bytes()) != entry["sha256"]:
            raise ValueError("Existing input differs; not overwritten: " + entry["path"])
        entries.append((source, destination))
    for source, destination in entries:
        if not destination.exists():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, destination)
    return {"inputs_checked": len(entries), "existing_versions_overwritten": False}


def publish_packet(directory, target):
    """Replace only the known incomplete legacy ZIP, never an unknown review packet."""
    verify_packet(directory)
    archive = directory.with_suffix(".zip")
    with zipfile.ZipFile(archive) as bundle:
        expected = {str(p.relative_to(directory)) for p in directory.rglob("*") if p.is_file()}
        if set(bundle.namelist()) != expected:
            raise ValueError("Archive coverage differs from verified directory")
        for name in expected:
            if bundle.read(name) != (directory / name).read_bytes():
                raise ValueError("Archive version differs: " + name)
    digest = sha(archive.read_bytes())
    if target.exists() and sha(target.read_bytes()) not in {
        read_json(LOCK)["prior_packet"]["sha256"],
        digest,
    }:
        raise ValueError("Existing legacy-path packet is unknown; not overwritten")
    with tempfile.NamedTemporaryFile(
        dir=target.parent, prefix="g1-review-", delete=False
    ) as stream:
        temporary = Path(stream.name)
        stream.write(archive.read_bytes())
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    return {"archive": str(target), "sha256": digest}


def validate_decisions(directory, answers, complete=False):
    manifest = verify_packet(directory)
    decisions = read_json(answers)
    if decisions["packet_id"] != manifest["packet_id"]:
        raise ValueError("Decisions belong to another packet/version")
    required = {r["id"] for r in read_json(directory / "review-rows.json")}
    ids = [r["id"] for r in decisions["rows"]]
    if len(ids) != len(set(ids)) or set(ids) != required:
        raise ValueError("Decision coverage/IDs differ")
    for r in decisions["rows"]:
        if r["decision"] not in DECISIONS or not isinstance(r["comment"], str):
            raise ValueError("Invalid human decision/comment")
    answered = sum(bool(r["decision"]) for r in decisions["rows"])
    if answered and (
        not decisions.get("reviewer", "").strip() or not decisions.get("reviewed_at", "").strip()
    ):
        raise ValueError("Reviewer and date must be supplied by the reviewer")
    if complete and answered != len(required):
        raise ValueError("Human review incomplete")
    return {
        "answered": answered,
        "total": len(required),
        "packet_id": manifest["packet_id"],
        "evidence_modified": False,
    }


def build(output):
    lock = read_json(LOCK)
    check_inputs(lock)
    if output.exists():
        raise ValueError("Output exists; choose another directory, never overwrite human work")
    if output.with_suffix(".zip").exists():
        raise ValueError("Archive exists; choose another output, never overwrite human work")
    analyses = [read_json(locked_path(lock["files"][key])) for key in ("analysis", "followup")]
    assert [a["id"] for a in analyses] == [lock["analysis_id"], lock["followup_id"]]
    snapshot = read_json(locked_path(lock["files"]["snapshot"]))
    documents, sources = validate_snapshot(*analyses, snapshot)
    output.mkdir(parents=True)
    for subdir in ("images", "originals", "frozen"):
        (output / subdir).mkdir()
    for key, dest in [
        ("analysis", "base.json"),
        ("followup", "followup.json"),
        ("snapshot", "database.json"),
        ("corpus_config", "corpus-config.json"),
    ]:
        shutil.copyfile(locked_path(lock["files"][key]), output / "frozen" / dest)
    shutil.copyfile(locked_path(lock["files"]["qa"]), output / "originals/official-qa.md")
    assets, pages, crops = render_pdfs(output, lock, snapshot, analyses)
    rows = make_rows(analyses, documents, sources, crops, assets)
    checked = add_company(output, lock, analyses, rows)
    add_page_context(output, rows, pages, assets, analyses)
    news = news_context(output, lock, snapshot, rows)
    # Every pinned input, including OCR boxes, travels with the packet. No DB,
    # network or another OCR run is needed to restore these local build inputs.
    rebuild = []
    destinations = {
        "analysis": "frozen/base.json",
        "followup": "frozen/followup.json",
        "snapshot": "frozen/database.json",
        "corpus_config": "frozen/corpus-config.json",
        "qa": "originals/official-qa.md",
    }
    for key, entry in lock["files"].items():
        rebuild.append({**entry, "packet_path": destinations[key]})
    for sid, entry in lock["pdfs"].items():
        rebuild.append({**entry, "packet_path": assets[sid]})
    for sid, entry in lock["ocr"].items():
        dest = "frozen/ocr/" + Path(entry["path"]).name
        (output / dest).parent.mkdir(exist_ok=True)
        shutil.copyfile(locked_path(entry), output / dest)
        rebuild.append({**entry, "packet_path": dest})
    rebuild.append(
        {
            **lock["company_csv"],
            "packet_path": "originals/" + Path(lock["company_csv"]["path"]).name,
        }
    )
    for name, group in lock["news"].items():
        for key, entry in group.items():
            dest = (
                f"originals/news/{name}/"
                + {"json": "receipt.json", "xml": "rss.xml.txt", "html": "article.html.txt"}[key]
            )
            if key == "json":
                shutil.copyfile(locked_path(entry), output / dest)
            rebuild.append({**entry, "packet_path": dest})
    (output / "rebuild-inputs.json").write_text(
        json.dumps(rebuild, ensure_ascii=False, indent=2) + "\n"
    )
    generator_sha = sha(Path(__file__).read_bytes())
    template_path = ROOT / "scripts/templates/g1_review.html"
    packet_id = sha(
        json.dumps(
            {"lock": lock, "generator": generator_sha, "template": sha(template_path.read_bytes())},
            sort_keys=True,
            ensure_ascii=False,
        ).encode()
    )
    payload = {
        "packet_id": packet_id,
        "reviewer": "",
        "reviewed_at": "",
        "rows": [{"id": r["id"], "decision": "", "comment": ""} for r in rows],
    }
    (output / "decisions-template.json").write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n"
    )
    (output / "review-rows.json").write_text(json.dumps(rows, ensure_ascii=False, indent=2) + "\n")
    (output / "input-lock.json").write_bytes(LOCK.read_bytes())
    report = {
        "classification": "agent_page_checked",
        "human_decisions_filled": 0,
        "claims": {"base": len(analyses[0]["claims"]), "followup": len(analyses[1]["claims"])},
        "evidence_ids": sorted(crops),
        "original_pdfs": len(assets),
        "original_pdf_pages": len(pages),
        "csv_cells_checked": checked,
        "news_not_in_g1_evidence": True,
        "no_model_calls": True,
        "observations": [
            "Первоначальный ZIP не содержал полный Analysis/связанный follow-up/второй PDF/CSV/новости.",
            "Выбран parent g1-analysis.json: другой g1-final-analysis.json не является родителем имеющегося follow-up.",
            "Macro p1: изображение показывает подписи таблицы и * Прогноз; сохранённый OCR chunk их полностью не передаёт. Данные не исправлялись.",
            "Условные выводы ссылаются на событие и LOA; условия договоров не представлены. Это предмет смысловой оценки человека.",
            "Pinned ACL=1, current ACL=2: старый API результат может дать 404. Права не менялись; полный результат доступен в frozen/base.json и index.html.",
            "Росстат: встроенный PNG графика сохранён напрямую из HTML. Числа изображения отсутствуют в текстовых Evidence; в этом проходе извлечение не менялось.",
        ],
    }
    (output / "agent-checks.json").write_text(
        json.dumps(report, ensure_ascii=False, indent=2) + "\n"
    )
    env = Environment(loader=FileSystemLoader(template_path.parent), autoescape=select_autoescape())
    env.policies["json.dumps_kwargs"] = {"sort_keys": True, "ensure_ascii": False}
    html_data = env.get_template(template_path.name).render(
        lock=lock,
        packet_id=packet_id,
        analyses=analyses,
        rows=rows,
        news=news,
        assets=assets,
        pages=[{"source": sid, "page": n, "image": p["image"]} for (sid, n), p in pages.items()],
        editor_json=json.dumps(payload, ensure_ascii=False).replace("<", "\\u003c"),
        sources=sources,
    )
    (output / "index.html").write_text(html_data)
    manifest = {
        "format_version": lock["format_version"],
        "source_revision": lock["source_revision"],
        "packet_id": packet_id,
        "analysis_id": analyses[0]["id"],
        "followup_id": analyses[1]["id"],
        "generator_sha256": generator_sha,
        "claims": 5,
        "evidence_count": len(crops),
        "review_rows": len(rows),
        "files": {
            str(p.relative_to(output)): sha(p.read_bytes())
            for p in sorted(output.rglob("*"))
            if p.is_file()
        },
    }
    (output / "manifest.json").write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
    verify_packet(output)
    archive = output.with_suffix(".zip")
    if archive.exists():
        raise ValueError("Archive exists; refusing overwrite")
    with zipfile.ZipFile(archive, "w", compression=zipfile.ZIP_DEFLATED) as bundle:
        for path in sorted(output.rglob("*")):
            if path.is_file():
                info = zipfile.ZipInfo(
                    str(path.relative_to(output)), date_time=(1980, 1, 1, 0, 0, 0)
                )
                info.compress_type = zipfile.ZIP_DEFLATED
                bundle.writestr(info, path.read_bytes())
    with zipfile.ZipFile(archive) as z:
        assert z.testzip() is None
    print(
        json.dumps(
            {
                "packet_id": packet_id,
                "archive": str(archive),
                "rows": len(rows),
                "claims": 5,
                "human_decisions": 0,
            },
            ensure_ascii=False,
        )
    )


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output", type=Path, default=Path("artifacts/integration/human-review"))
    parser.add_argument("--verify", action="store_true")
    parser.add_argument("--decisions", type=Path)
    parser.add_argument("--require-complete", action="store_true")
    parser.add_argument("--restore-inputs", type=Path)
    parser.add_argument(
        "--publish-legacy",
        action="store_true",
        help="Replace the SHA-known incomplete g1-review.zip after verification",
    )
    args = parser.parse_args()
    try:
        if args.restore_inputs:
            print(json.dumps(restore_inputs(args.restore_inputs)))
        elif args.decisions:
            print(
                json.dumps(
                    validate_decisions(args.output, args.decisions, args.require_complete),
                    ensure_ascii=False,
                )
            )
        elif args.verify:
            manifest = verify_packet(args.output)
            print(
                json.dumps({k: v for k, v in manifest.items() if k != "files"}, ensure_ascii=False)
            )
        else:
            build(args.output)
        if args.publish_legacy:
            if args.decisions or args.restore_inputs:
                raise ValueError("Publish is only allowed with build/verify")
            print(
                json.dumps(
                    publish_packet(args.output, ROOT / "artifacts/integration/g1-review.zip")
                )
            )
    except (ValueError, AssertionError, KeyError) as exc:
        raise SystemExit(str(exc) or "Review invariant failed") from None


if __name__ == "__main__":
    main()
