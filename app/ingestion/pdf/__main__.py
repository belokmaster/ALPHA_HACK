"""Explicit local-only PDF import, independent of CSV. No startup side effects."""

import argparse
import asyncio
import json
from pathlib import Path

from app.ingestion.pdf.pipeline import save_bank_theses
from app.ingestion.pdf.store import prepare_corpus, publish_corpus


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--pdf-dir", type=Path, default=Path("raw/pdf"))
    parser.add_argument("--cache-dir", type=Path, default=Path("artifacts/ocr"))
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--lexical-only", action="store_true")
    args = parser.parse_args()
    prepared = prepare_corpus(args.pdf_dir, cache_dir=args.cache_dir)
    from app.retrieval.service import LocalE5Encoder

    encoder = LocalE5Encoder(force_disable=args.lexical_only)
    if not args.lexical_only and not encoder.is_available:
        raise SystemExit("Pinned local E5 required; prepare model or explicitly --lexical-only")

    async def run():
        from app.core.db import engine

        try:
            result = await publish_corpus(engine, prepared, encoder)
            save_bank_theses(
                [th for p in prepared for th in p[3]], Path("artifacts/manifests/bank_theses.json")
            )
            manifest = {
                "result": result,
                "encoder_available": encoder.is_available,
                "documents": [
                    {
                        "source": source.model_dump(mode="json"),
                        "document_id": doc.id,
                        "published_at": doc.published_at.isoformat() if doc.published_at else None,
                        "available_at": None,
                        "evidence_count": len(evidence),
                    }
                    for source, doc, evidence, _ in prepared
                ],
            }
            output = Path("artifacts/manifests/pdf-import.json")
            output.parent.mkdir(parents=True, exist_ok=True)
            output.write_text(json.dumps(manifest, ensure_ascii=False, indent=2) + "\n")
            print(json.dumps(result))
        finally:
            await engine.dispose()

    if args.apply:
        asyncio.run(run())
    else:
        print(
            json.dumps(
                {
                    "status": "prepared_only",
                    "documents": len(prepared),
                    "evidence": sum(len(p[2]) for p in prepared),
                }
            )
        )


if __name__ == "__main__":
    main()
