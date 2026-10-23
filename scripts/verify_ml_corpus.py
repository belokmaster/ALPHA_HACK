"""Real OCR/E5/pgvector acceptance probe. Explicit isolated DB only; no paid APIs."""

import asyncio
import hashlib
import json
import os
import resource
import time
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import create_async_engine

from app.auth.service import current_context
from app.core import tables as t
from app.ingestion.pdf.store import IMPORT_VERSION, prepare_corpus, publish_corpus
from app.retrieval.service import HybridRetriever, LocalE5Encoder
from contracts.v1 import AnalysisRequest
from eval.run import compare_diagnostics


class MemoEncoder:
    """Measure a single real encode; reuse identical vectors in rollback/repeat probes."""

    def __init__(self, encoder):
        self.encoder = encoder
        self.is_available = encoder.is_available
        self.cache = {}
        self.seconds = 0.0

    def encode_passages(self, texts):
        key = tuple(texts)
        if key not in self.cache:
            start = time.perf_counter()
            self.cache[key] = self.encoder.encode_passages(texts)
            self.seconds += time.perf_counter() - start
        return self.cache[key]


async def probe():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if url.rsplit("/", 1)[-1] != "alfa_test":
        raise SystemExit("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    started = time.perf_counter()
    prepared = prepare_corpus(Path("raw/pdf"), cache_dir=Path("artifacts/ocr"))
    encoder = LocalE5Encoder()
    if not encoder.is_available:
        raise SystemExit("Real pinned local E5 required, not fixtures/fallback")
    memo = MemoEncoder(encoder)
    engine = create_async_engine(url)
    try:

        async def count():
            async with engine.connect() as c:
                return (
                    await c.execute(
                        select(func.count())
                        .select_from(t.documents)
                        .join(t.source_versions, t.documents.c.source_id == t.source_versions.c.id)
                        .where(t.source_versions.c.import_version == IMPORT_VERSION)
                    )
                ).scalar_one()

        if await count():
            raise SystemExit("Probe requires a new isolated DB with no prior bank corpus")
        observations = []

        async def fail(c, number):
            if number == 8:
                observations.append(await count())
                raise RuntimeError("Injected late publication failure")

        try:
            await publish_corpus(engine, prepared, memo, checkpoint=fail)
            raise AssertionError("Fault injection did not run")
        except RuntimeError as exc:
            assert str(exc) == "Injected late publication failure"
        assert observations == [0] and await count() == 0
        apply = await publish_corpus(engine, prepared, memo)
        repeat = await publish_corpus(engine, prepared, memo)
        assert apply["imported"] == 9 and repeat["imported"] == 0 and repeat["unchanged"] == 9
        assert await count() == 9
        # Exact quotes are reconstructable from cached page text and exposed spans.
        reconstructed = 0
        for _, doc, evidence, _ in prepared:
            for ev in evidence:
                _, start, end = ev.span.split(":")
                marker = f"[Стр. {ev.page}]\n"
                page = doc.text.split(marker, 1)[1].split("\n\n[Стр.", 1)[0]
                assert page[int(start) : int(end)] == ev.quote
                reconstructed += 1
        async with engine.connect() as c:
            ctx = await current_context(c, "alice")
        retriever = HybridRetriever(engine, encoder)
        req = AnalysisRequest(
            event_id="bank_probe",
            context_version=ctx.version,
            as_of="2026-10-22T00:00:00Z",
            temporal_mode="strict_as_of",
        )
        strict = await retriever.search(ctx, req, "ключевая ставка")
        assert not strict  # no supplied PDF has proved historical availability
        comparisons = await compare_diagnostics(engine, encoder, ctx)
        for row in comparisons["modes"]["hnsw"]["queries"]:
            assert row["mode"] == "hybrid_hnsw" and row["hnsw_index_used"]
        assert all(
            r["mode"] == "hybrid_exact" and not r["hnsw_index_used"]
            for r in comparisons["modes"]["exact"]["queries"]
        )
        result = {
            "status": "technical_checks_passed",
            "seconds": time.perf_counter() - started,
            "peak_process_rss_kib": resource.getrusage(resource.RUSAGE_SELF).ru_maxrss,
            "real_passage_encoding_seconds": memo.seconds,
            "fault_observations": observations,
            "rollback_bank_documents": 0,
            "apply": apply,
            "repeat": repeat,
            "reconstructed_spans": reconstructed,
            "strict_as_of_bank_candidates": len(strict),
            "comparisons": comparisons,
            "model_manifest_sha256": hashlib.sha256(Path("models.lock").read_bytes()).hexdigest(),
            "corpus_config_sha256": hashlib.sha256(
                Path("config/bank_corpus.json").read_bytes()
            ).hexdigest(),
        }
        out = Path("artifacts/ml-remediation/corpus-probe.json")
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n")
        print(
            json.dumps({k: v for k, v in result.items() if k != "comparisons"}, ensure_ascii=False)
        )
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(probe())
