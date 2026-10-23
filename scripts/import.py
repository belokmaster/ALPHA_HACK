import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from app.core.db import engine
from app.ingestion.csv.importer import run as import_csv


async def run_import(
    data_dir: Path,
    company_scope_id: str = "__public__",
    *,
    apply: bool = False,
    pdf_dir: Path | None = None,
) -> None:
    manifests_dir = Path("./artifacts/manifests")
    manifests_dir.mkdir(parents=True, exist_ok=True)

    result = await import_csv(data_dir, manifests_dir / "csv-import.json", apply)
    print("CSV:", result["status"])
    if not apply or pdf_dir is None:
        return
    if company_scope_id != "__public__":
        raise ValueError("Bank PDF corpus requires explicit shared __public__ scope")
    import subprocess

    subprocess.run(
        [sys.executable, "-m", "app.ingestion.pdf", "--pdf-dir", str(pdf_dir), "--apply"],
        check=True,
    )
    await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Explicit CSV import, optional local PDF stage")
    parser.add_argument("input_dir", type=Path)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--pdf-dir", type=Path)
    parser.add_argument("--company-scope-id", default="__public__")
    args = parser.parse_args()
    asyncio.run(
        run_import(args.input_dir, args.company_scope_id, apply=args.apply, pdf_dir=args.pdf_dir)
    )
