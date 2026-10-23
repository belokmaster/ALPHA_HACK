"""Verify authored G1/G2/G3 source records without importing a working database."""

import csv
import hashlib
import json
from pathlib import Path


def main():
    cases = json.loads(Path("docs/gold/manual-cases.json").read_text())
    refs = []
    for case in cases["cases"]:
        refs += case.get("profile_refs", []) + case.get("payment_refs", [])
        if "counterfactual" in case:
            refs.append(case["counterfactual"])
    report = {}
    for filename in sorted({r["file"] for r in refs}):
        p = Path("raw/csv") / filename
        wanted = {r["row"]: r["expect"] for r in refs if r["file"] == filename}
        with p.open() as stream:
            for n, row in enumerate(csv.DictReader(stream, delimiter=";"), 2):
                if n in wanted:
                    expected = wanted.pop(n)
                    if not all(row[k] == v for k, v in expected.items()):
                        raise ValueError(f"Manual case input differs: {filename}:{n}")
        if wanted:
            raise ValueError("Missing manual-case input records")
        report[filename] = {
            "sha256": hashlib.sha256(p.read_bytes()).hexdigest(),
            "verified_refs": sum(r["file"] == filename for r in refs),
        }
    print(json.dumps(report, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
