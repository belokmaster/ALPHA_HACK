import json
import sys
from pathlib import Path

from eval.cases.gold_v1 import (
    GOLD_RETRIEVAL_QUERIES,
    GOLD_SCENARIOS,
    GoldRetrievalQuery,
)

DEFAULT_SPLITS_PATH = Path(__file__).resolve().parent / "splits.json"
DEFAULT_CORPUS_PATH = Path(__file__).resolve().parents[1] / "config/bank_corpus.json"


def validate_gold(
    splits_path: Path = DEFAULT_SPLITS_PATH,
    corpus_path: Path | None = DEFAULT_CORPUS_PATH,
) -> dict:
    errors: list[str] = []
    warnings: list[str] = []

    # 1. Проверка splits.json
    if not splits_path.exists():
        raise FileNotFoundError(f"splits.json not found at {splits_path}")

    splits_data = json.loads(splits_path.read_text(encoding="utf-8"))
    splits = splits_data.get("splits", {})
    required_splits = {"dev", "validation", "test"}
    if not required_splits.issubset(set(splits.keys())):
        errors.append(f"splits.json missing required splits. Expected: {required_splits}")

    event_to_split: dict[str, str] = {}
    for s_name, events in splits.items():
        for ev_id in events:
            if ev_id in event_to_split:
                errors.append(
                    f"Duplicate event_id '{ev_id}' found across splits: {event_to_split[ev_id]} and {s_name}"
                )
            event_to_split[ev_id] = s_name

    # 2. Проверка GOLD_SCENARIOS
    seen_case_ids: set[str] = set()
    for sc in GOLD_SCENARIOS:
        if sc.case_id in seen_case_ids:
            errors.append(f"Duplicate case_id in GOLD_SCENARIOS: {sc.case_id}")
        seen_case_ids.add(sc.case_id)

        if sc.event_id not in event_to_split:
            errors.append(
                f"Scenario '{sc.case_id}' references event_id '{sc.event_id}' not found in splits.json"
            )
        elif sc.split != event_to_split[sc.event_id]:
            errors.append(
                f"Scenario '{sc.case_id}' split '{sc.split}' does not match splits.json '{event_to_split[sc.event_id]}'"
            )

        if not sc.expected_question_substring:
            errors.append(f"Scenario '{sc.case_id}' missing expected_question_substring")

    # 3. Проверка GOLD_RETRIEVAL_QUERIES
    seen_query_ids: set[str] = set()
    queries_by_split: dict[str, list[GoldRetrievalQuery]] = {
        "dev": [],
        "validation": [],
        "test": [],
    }

    for q in GOLD_RETRIEVAL_QUERIES:
        if q.query_id in seen_query_ids:
            errors.append(f"Duplicate query_id in GOLD_RETRIEVAL_QUERIES: {q.query_id}")
        seen_query_ids.add(q.query_id)

        if q.event_id not in event_to_split:
            errors.append(
                f"Query '{q.query_id}' references event_id '{q.event_id}' not found in splits.json"
            )
        elif q.split != event_to_split[q.event_id]:
            errors.append(
                f"Query '{q.query_id}' split '{q.split}' does not match splits.json '{event_to_split[q.event_id]}'"
            )

        if q.split in queries_by_split:
            queries_by_split[q.split].append(q)

        # Answerable / No-answer контракты
        if q.answerable:
            if not q.gold_groups:
                errors.append(f"Answerable query '{q.query_id}' has empty gold_groups")
            for grp in q.gold_groups:
                if not grp.acceptable_doc_ids:
                    errors.append(
                        f"Query '{q.query_id}' group '{grp.group_id}' has empty acceptable_doc_ids"
                    )
                # Конфликт исключений и допустимых ID
                intersection = set(grp.acceptable_doc_ids).intersection(set(q.must_exclude_doc_ids))
                if intersection:
                    errors.append(
                        f"Query '{q.query_id}' group '{grp.group_id}' has docs in both acceptable and must_exclude: {intersection}"
                    )
        else:
            if q.gold_groups:
                errors.append(f"No-answer query '{q.query_id}' must have empty gold_groups")

    # 4. Проверка сбалансированности сплитов
    dev_answerable = [q for q in queries_by_split["dev"] if q.answerable]
    test_answerable = [q for q in queries_by_split["test"] if q.answerable]
    test_no_answer = [q for q in queries_by_split["test"] if not q.answerable]

    if not dev_answerable:
        errors.append("Dev split has 0 answerable queries")
    if not test_answerable:
        errors.append("Test split has 0 answerable queries; gate would be uninformative")
    if not test_no_answer:
        errors.append("Test split has 0 no-answer queries; abstention gate cannot be tested")

    # 5. Проверка согласованности с config/bank_corpus.json (если файл существует)
    corpus_docs: set[str] = set()
    if corpus_path and corpus_path.exists():
        try:
            corpus_data = json.loads(corpus_path.read_text(encoding="utf-8"))
            for entry in corpus_data.get("documents", {}).values():
                corpus_docs.add(entry["doc_id"])
            for q in GOLD_RETRIEVAL_QUERIES:
                for grp in q.gold_groups:
                    for d in grp.acceptable_doc_ids:
                        if d not in corpus_docs:
                            warnings.append(
                                f"Query '{q.query_id}' acceptable doc '{d}' not in bank_corpus.json documents"
                            )
        except Exception as e:
            warnings.append(f"Failed to check corpus_path '{corpus_path}': {e}")
    else:
        warnings.append(f"Corpus path '{corpus_path}' does not exist yet")

    report = {
        "valid": len(errors) == 0,
        "scenarios_count": len(GOLD_SCENARIOS),
        "queries_count": len(GOLD_RETRIEVAL_QUERIES),
        "dev_queries": len(queries_by_split["dev"]),
        "validation_queries": len(queries_by_split["validation"]),
        "test_queries": len(queries_by_split["test"]),
        "test_answerable": len(test_answerable),
        "test_no_answer": len(test_no_answer),
        "errors": errors,
        "warnings": warnings,
    }

    if errors:
        raise ValueError(
            f"Gold validation failed with {len(errors)} error(s):\n"
            + "\n".join(f"- {e}" for e in errors)
        )

    blockers = []
    if {k: len(v) for k, v in splits.items()} != {"dev": 8, "validation": 4, "test": 12}:
        blockers.append(
            "Need 24 event groups with chronological 8/4/12 splits; existing set is exposed development smoke"
        )
    if (
        [len(queries_by_split[k]) for k in ("dev", "validation", "test")] != [8, 4, 12]
        or len(test_answerable) != 10
        or len(test_no_answer) != 2
    ):
        blockers.append("Need 24 queries with 10 answerable +2 no-answer independent test")
    if sum(s.split == "test" for s in GOLD_SCENARIOS) < 12:
        blockers.append(
            "Need 12 test business scenarios; current G1/G2/G3 are agent-authored analyses"
        )
    if (
        splits_data.get("status") != "human_reviewed_frozen"
        or splits_data.get("independent_human_review") != "complete"
    ):
        blockers.append(
            "A/B must select, freeze and independently review gold; >=20% and all critical labels need second review"
        )
    manifest_name = splits_data.get("chronological_source_manifest")
    if not manifest_name:
        blockers.append(
            "Need source-backed events.manifest.json to verify chronological source/event grouping"
        )
    else:
        try:
            from datetime import datetime

            manifest = (splits_path.parent / manifest_name).resolve()
            if not manifest.is_relative_to(DEFAULT_CORPUS_PATH.parent.parent):
                raise ValueError("manifest outside repo")
            events = json.loads(manifest.read_text())["events"]
            if {e["event_id"] for e in events} != set(event_to_split):
                raise ValueError("event coverage mismatch")
            dates = {k: [] for k in required_splits}
            groups = {}
            for event in events:
                split = event_to_split[event["event_id"]]
                date = datetime.fromisoformat(event["published_at"])
                if date.tzinfo is None or len(event["source_sha256"]) != 64:
                    raise ValueError("source date/hash not evidenced")
                dates[split].append(date)
                group = event["group_id"]
                if group in groups and groups[group] != split:
                    raise ValueError("event versions/paraphrases cross splits")
                groups[group] = split
            if not (
                max(dates["dev"]) < min(dates["validation"])
                and max(dates["validation"]) < min(dates["test"])
            ):
                raise ValueError("splits are not chronological")
        except (OSError, ValueError, KeyError, TypeError):
            blockers.append(
                "Source manifest missing/invalid or splits not chronological/group-disjoint"
            )
    try:
        import hashlib
        import math

        review_path = splits_path.parent / "human-review.json"
        reviews = json.loads(review_path.read_text())
        if reviews["splits_sha256"] != hashlib.sha256(splits_path.read_bytes()).hexdigest():
            raise ValueError("review does not match frozen splits")
        rows = reviews["annotations"]
        if {r["query_id"] for r in rows} != seen_query_ids or len(rows) != len(seen_query_ids):
            raise ValueError("review coverage mismatch")
        second = 0
        for row in rows:
            people = [r["name"] for r in row["reviewers"] if r.get("kind") == "human"]
            if not people or len(set(people)) != len(people):
                raise ValueError("independent human attribution missing")
            double = len(people) >= 2
            second += double
            if row.get("critical") and not double:
                raise ValueError("critical annotation needs second review")
        if second < math.ceil(0.2 * len(rows)):
            raise ValueError("second review below 20%")
    except (OSError, ValueError, KeyError, TypeError):
        blockers.append(
            "Need human-review.json with matching splits SHA, all query labels, >=20% second review and all critical claims adjudicated"
        )
    if any(
        not group.acceptable_spans
        for q in GOLD_RETRIEVAL_QUERIES
        if q.answerable
        for group in q.gold_groups
    ):
        blockers.append(
            "Need verified span-level answer groups, not only document/page smoke labels"
        )
    if corpus_path and corpus_path.exists():
        cdata = json.loads(corpus_path.read_text(encoding="utf-8"))
        docs = {e["doc_id"]: e for e in cdata["documents"].values()}
        impossible = [
            q.query_id
            for q in GOLD_RETRIEVAL_QUERIES
            if q.answerable
            and q.temporal_mode.value == "strict_as_of"
            and any(
                not docs.get(d, {}).get("available_at")
                for g in q.gold_groups
                for d in g.acceptable_doc_ids
            )
        ]
        if impossible:
            blockers.append(
                "Strict answerable queries lack proved source availability: " + ",".join(impossible)
            )
    report["acceptance_ready"] = not blockers
    report["acceptance_blockers"] = blockers
    return report


if __name__ == "__main__":
    try:
        res = validate_gold()
        print(
            f"Gold validation SUCCESS: {res['scenarios_count']} scenarios, {res['queries_count']} queries."
        )
        print(
            f"Splits: dev={res['dev_queries']}, val={res['validation_queries']}, test={res['test_queries']} (ans={res['test_answerable']}, no-ans={res['test_no_answer']})."
        )
        if res["warnings"]:
            print("Warnings:")
            for w in res["warnings"]:
                print(f"  [WARN] {w}")
        if "--acceptance" in sys.argv:
            if not res["acceptance_ready"]:
                print("BLOCKED: " + "; ".join(res["acceptance_blockers"]))
                sys.exit(2)
            print("Acceptance inputs complete; measured retrieval gate still required.")
            sys.exit(0)
        print("Development smoke only; acceptance BLOCKED (not full gold).")
        sys.exit(0)
    except Exception as exc:
        print(f"Gold validation ERROR: {exc}", file=sys.stderr)
        sys.exit(1)
