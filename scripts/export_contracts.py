import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from app.main import app
from contracts import v1
from contracts.examples import examples


def artifacts():
    schemas = {
        name: cls.model_json_schema(mode="serialization")
        for name, cls in vars(v1).items()
        if isinstance(cls, type) and issubclass(cls, v1.DTO)
    }
    return {"contracts/openapi.json": app.openapi(), "contracts/schemas.json": schemas}


if __name__ == "__main__":
    for name, data in artifacts().items():
        content = json.dumps(data, ensure_ascii=False, sort_keys=True, indent=2) + "\n"
        path = Path(name)
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Stale contract: {name}; make export-contracts")
        else:
            path.write_text(content)

    example_data = [
        {"contract": type(obj).__name__, "value": obj.model_dump(mode="json")} for obj in examples()
    ]
    outputs = {
        "contracts/examples.json": json.dumps(example_data, ensure_ascii=False, indent=2) + "\n",
        "API_EXAMPLES.md": "# API examples · schema 1.1.0 (reads legacy 1.0.0)\n\nLogin → save cookie and csrf_token → GET context → PATCH with expected_version → POST analyses with X-CSRF-Token and a unique Idempotency-Key → poll poll_url → GET analyses/result_id. Followups use the original analysis context_version. IDs below are examples; use returned IDs. All bodies below are validated by tests.\n\n"
        + "\n".join(
            "## "
            + v["contract"]
            + "\n\n```json\n"
            + json.dumps(v["value"], ensure_ascii=False, indent=2)
            + "\n```\n"
            for v in example_data
        ),
    }
    for name, content in outputs.items():
        path = Path(name)
        if "--check" in sys.argv:
            if not path.exists() or path.read_text() != content:
                raise SystemExit(f"Stale examples: {name}")
        else:
            path.write_text(content)
