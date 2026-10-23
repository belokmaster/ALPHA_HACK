"""Server-authorized local E5 + PostgreSQL retrieval; public port remains list[Evidence]."""

import hashlib
import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from sqlalchemy import text

from app.auth.service import authorize, current_context, visible_profiles
from app.core.errors import AppError
from app.ingestion.pdf.pipeline import document_id, load_corpus_config
from app.ingestion.pdf.store import IMPORT_VERSION, PUBLIC_SCOPE, corpus_document_ids
from contracts.ports import EvidenceRetriever
from contracts.v1 import AnalysisRequest, BankThesis, ErrorCode, Evidence, UserContext

logger = logging.getLogger(__name__)
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_RETRIEVAL_CONFIG = ROOT / "config/retrieval.json"
E5_MODEL_ID = "intfloat/multilingual-e5-small"
E5_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
RetrievalMode = Literal["hybrid_hnsw", "hybrid_exact", "lexical_fallback"]


def load_retrieval_config(config_path=DEFAULT_RETRIEVAL_CONFIG):
    return json.loads(config_path.read_text())


class LocalE5Encoder:
    def __init__(self, cache_dir=None, force_disable=False, config_path=DEFAULT_RETRIEVAL_CONFIG):
        self.model = None
        self.manifest = None
        if force_disable:
            return
        cfg = load_retrieval_config(config_path)
        if cfg.get("download_on_startup") is not False or cfg.get("local_only") is not True:
            raise ValueError("Runtime model downloads prohibited")
        try:
            manifest = json.loads((ROOT / "models.lock").read_text())
            if manifest["revision"] != E5_REVISION or manifest["model_id"] != E5_MODEL_ID:
                raise ValueError("Unexpected E5 revision")
            path = (ROOT / manifest["path"]).resolve()
            if not path.is_relative_to(ROOT / "models"):
                raise ValueError("Model path outside repository model directory")
            for name, digest in manifest["files"].items():
                file = (path / name).resolve()
                if (
                    not file.is_relative_to(path)
                    or hashlib.sha256(file.read_bytes()).hexdigest() != digest
                ):
                    raise ValueError("Model file hash mismatch")
            from sentence_transformers import SentenceTransformer

            self.model = SentenceTransformer(
                str(path),
                device="cpu",
                trust_remote_code=False,
                local_files_only=True,
                model_kwargs={"use_safetensors": True},
            )
            self.model.max_seq_length = 512
            self.model.float()
            self.manifest = manifest
        except (ImportError, OSError, ValueError, KeyError) as exc:
            logger.warning("Pinned local E5 unavailable (%s); lexical fallback", type(exc).__name__)
            self.model = None

    @property
    def is_available(self):
        return self.model is not None

    def encode_query(self, query):
        if self.model is None:
            raise RuntimeError("Encoder unavailable")
        return (
            self.model.encode(["query: " + query], normalize_embeddings=True)[0]
            .astype("float32")
            .tolist()
        )

    def encode_passages(self, passages, batch_size=8):
        if self.model is None:
            raise RuntimeError("Encoder unavailable")
        return (
            self.model.encode(
                ["passage: " + p for p in passages],
                batch_size=batch_size,
                normalize_embeddings=True,
            )
            .astype("float32")
            .tolist()
        )


@dataclass(frozen=True)
class DetailedSearchResult:
    evidence: list[Evidence]
    retrieval_mode: RetrievalMode
    theses: list[BankThesis] = field(default_factory=list)
    index_version: str = f"pgvector-e5-{E5_REVISION[:8]}"
    degraded: bool = False
    warnings: list[str] = field(default_factory=list)
    query_plan: dict | None = None


@dataclass(frozen=True)
class RetrievalPolicy:
    # Trusted task configuration, not parsed from query text or supplied by the model.
    allow_historical: bool = False


def compute_rrf_and_deduplicate(
    fts_rows: list[dict],
    vec_rows: list[dict],
    limit: int = 8,
    rrf_k: float = 60.0,
    max_per_page: int = 3,
) -> list[Evidence]:
    """Слияние Reciprocal Rank Fusion: RRF(d) = Σ 1 / (k + rank_i(d)) с дедупликацией по цитате и квотой страниц."""
    rrf_scores: dict[str, float] = {}
    payload_by_id: dict[str, dict] = {}

    for rank, row in enumerate(fts_rows, start=1):
        eid = row["id"]
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + 1.0 / (rrf_k + rank)
        payload_by_id[eid] = row["payload"]

    for rank, row in enumerate(vec_rows, start=1):
        eid = row["id"]
        rrf_scores[eid] = rrf_scores.get(eid, 0.0) + 1.0 / (rrf_k + rank)
        payload_by_id[eid] = row["payload"]

    # Стабильная сортировка по RRF-скору (по убыванию), затем по id (по возрастанию)
    sorted_ids = sorted(rrf_scores.keys(), key=lambda k: (-rrf_scores[k], k))

    # Дедупликация по хешу цитаты и квота <= max_per_page чанков с одной страницы
    seen_quote_hashes: set[str] = set()
    page_counts: dict[tuple[str, int | None], int] = {}
    final_evidence: list[Evidence] = []

    for eid in sorted_ids:
        ev = Evidence.model_validate(payload_by_id[eid])
        q_hash = hashlib.sha256(ev.quote.strip().encode("utf-8")).hexdigest()
        if q_hash in seen_quote_hashes:
            continue

        page_key = (ev.document_version_id, ev.page)
        if ev.page is not None and page_counts.get(page_key, 0) >= max_per_page:
            continue

        seen_quote_hashes.add(q_hash)
        page_counts[page_key] = page_counts.get(page_key, 0) + 1
        final_evidence.append(ev)

        if len(final_evidence) >= limit:
            break

    return final_evidence


class HybridRetriever(EvidenceRetriever):
    def __init__(self, engine, encoder, *, policy=RetrievalPolicy()):
        self.engine, self.encoder, self.policy = engine, encoder, policy
        self.pgvector_supported = True

    async def ensure_pgvector_schema(self):
        async with self.engine.connect() as c:
            await c.execute(text("SELECT embedding, fts_doc FROM evidence LIMIT 0"))
        return True

    async def search_with_meta(
        self,
        context,
        request,
        query,
        limit=8,
        mode_preference="hnsw",
        domain="case",
        hnsw_ef_search=40,
        allow_historical=None,
        explain=False,
    ):
        context = context.model_copy(deep=True)
        request = request.model_copy(deep=True)
        if not 1 <= limit <= 100 or mode_preference not in {"hnsw", "exact", "lexical"}:
            raise ValueError("Invalid retrieval limit or mode")
        if domain not in {"case", "synthetic", "live"} or not 1 <= hnsw_ef_search <= 1000:
            raise ValueError("Invalid retrieval domain or ef_search")
        historical = self.policy.allow_historical if allow_historical is None else allow_historical
        vectors = self.encoder.is_available and mode_preference != "lexical"
        # CPU inference outside the DB transaction. Authorization is still checked after
        # inference and before any data access; current grants are never taken from ctx.
        vector = None
        if vectors:
            try:
                vector = str(self.encoder.encode_query(query))
            except Exception:
                logger.warning("Local query encoding failed; lexical fallback")
                vectors = False
        config = load_corpus_config()
        historical_ids = [
            x
            for e in config["documents"].values()
            if e.get("is_historical")
            for x in (e["doc_id"], document_id(e, config))
        ]
        plan = None
        mode = (
            ("hybrid_exact" if mode_preference == "exact" else "hybrid_hnsw")
            if vectors
            else "lexical_fallback"
        )
        async with self.engine.begin() as c:
            await authorize(c, context, context.company_id, "sources.read")
            current = await current_context(c, context.principal_id)
            if (
                request.context_version != current.version
                or request.context_version != context.version
                or context.id != current.id
                or context.preferences != current.preferences
                or context.company_id != current.company_id
                or context.role != current.role
                or context.acl_version != current.acl_version
            ):
                raise AppError(ErrorCode.context_stale, "Context changed", 409)
            allowed = [p.company_id for p in await visible_profiles(c, context, "sources.read")]
            # Explicit registry membership and import version, not just NULL/company name.
            eligible = text("""SELECT d.id FROM documents d JOIN source_versions s ON s.id=d.source_id
                WHERE (d.company_id=ANY(:allowed) OR
                  (d.company_id=:public AND d.id=ANY(:bank_ids) AND s.import_version=:bank_version))
                AND d.payload->>'domain'=:domain
                AND s.payload->>'domain'=:domain
                AND (:historical OR NOT(d.id=ANY(:historical_ids)))
                AND (d.payload->>'published_at' IS NULL OR (d.payload->>'published_at')::timestamptz<=:as_of)
                AND ((d.payload->>'available_at' IS NOT NULL AND (d.payload->>'available_at')::timestamptz<=:as_of)
                     OR (:snapshot AND d.payload->>'available_at' IS NULL))
                AND ((s.payload->>'available_at' IS NOT NULL AND (s.payload->>'available_at')::timestamptz<=:as_of)
                     OR (:snapshot AND s.payload->>'available_at' IS NULL))""")
            params = {
                "allowed": allowed,
                "public": PUBLIC_SCOPE,
                "bank_ids": corpus_document_ids(config),
                "bank_version": IMPORT_VERSION,
                "historical": historical,
                "historical_ids": historical_ids,
                "domain": domain,
                "as_of": request.as_of,
                "snapshot": request.temporal_mode.value == "snapshot_demo",
            }
            ids = list((await c.execute(eligible, params)).scalars())
            params.update(ids=ids, q=query, vector=vector)
            where = """WHERE e.document_id=ANY(:ids) AND e.payload->>'domain'=:domain
                AND ((e.payload->>'available_at' IS NOT NULL AND (e.payload->>'available_at')::timestamptz<=:as_of)
                     OR (:snapshot AND e.payload->>'available_at' IS NULL))"""
            fts = f"""SELECT e.id,e.payload FROM evidence e {where}
                AND e.fts_doc @@ websearch_to_tsquery('russian',:q)
                ORDER BY ts_rank_cd(e.fts_doc,websearch_to_tsquery('russian',:q)) DESC,e.id LIMIT 20"""
            if explain:
                plan = {
                    "fts": (
                        await c.execute(
                            text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + fts), params
                        )
                    ).scalar_one(),
                    "vector": None,
                }
            fts_rows = (await c.execute(text(fts), params)).mappings().all()
            vec_rows = []
            if vectors:
                sql = f"""SELECT e.id,e.payload FROM evidence e {where} AND e.embedding IS NOT NULL
                    ORDER BY e.embedding <=> CAST(:vector AS vector) LIMIT 20"""
                try:
                    async with c.begin_nested():
                        if mode_preference == "exact":
                            await c.execute(text("SET LOCAL enable_indexscan=off"))
                            await c.execute(text("SET LOCAL enable_bitmapscan=off"))
                        else:
                            # Explicit mode: use ANN even on a small corpus. EXPLAIN reports
                            # this forced setting; it is not a claim about planner's default cost.
                            await c.execute(text("SET LOCAL enable_seqscan=off"))
                            await c.execute(text("SET LOCAL enable_bitmapscan=off"))
                            await c.execute(text(f"SET LOCAL hnsw.ef_search={int(hnsw_ef_search)}"))
                            await c.execute(text("SET LOCAL hnsw.iterative_scan='strict_order'"))
                        if explain:
                            plan["vector"] = (
                                await c.execute(
                                    text("EXPLAIN (ANALYZE, BUFFERS, FORMAT JSON) " + sql), params
                                )
                            ).scalar_one()
                        vec_rows = (await c.execute(text(sql), params)).mappings().all()
                except Exception:
                    logger.warning("Vector query failed; lexical fallback")
                    mode = "lexical_fallback"
        evidence = compute_rrf_and_deduplicate(fts_rows, vec_rows, limit=limit)
        # No filesystem thesis fallback, hidden hardcoded rates or current thesis from an
        # older source. Until reviewed spans exist, candidates are explicitly unverified.
        warnings = ["snapshot_demo_is_not_historical_backtest"] if params["snapshot"] else []
        if evidence:
            warnings.append("OCR_candidates_are_not_verified_bank_theses")
        degraded = mode == "lexical_fallback" and mode_preference != "lexical"
        if degraded:
            warnings.append("local_E5_or_vector_search_unavailable")
        return DetailedSearchResult(
            evidence, mode, degraded=degraded, warnings=warnings, query_plan=plan
        )

    async def search(
        self,
        context: UserContext,
        request: AnalysisRequest,
        query: str,
        limit=8,
        allow_historical=None,
    ) -> list[Evidence]:
        return (
            await self.search_with_meta(
                context, request, query, limit=limit, allow_historical=allow_historical
            )
        ).evidence

    async def explain_query_plan(self, context, request, query, mode_preference="hnsw"):
        result = await self.search_with_meta(
            context, request, query, mode_preference=mode_preference, explain=True
        )
        return {
            "plan": result.query_plan,
            "mode": result.retrieval_mode,
            "hnsw_used": "idx_evidence_hnsw" in json.dumps(result.query_plan),
            "forced_ann": mode_preference == "hnsw",
        }
