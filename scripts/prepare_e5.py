"""Explicit free model preparation; never imported or called by app startup."""

import argparse
import hashlib
import json
from pathlib import Path

MODEL = "intfloat/multilingual-e5-small"
REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
DEST = Path("models/e5") / REVISION


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--download", action="store_true")
    args = parser.parse_args()
    if args.download:
        from huggingface_hub import snapshot_download

        snapshot_download(
            MODEL,
            revision=REVISION,
            local_dir=str(DEST),
            token=False,
            allow_patterns=[
                "*.json",
                "*.txt",
                "*.model",
                "*.safetensors",
                "1_Pooling/*",
                "README.md",
            ],
        )
    files = {
        str(p.relative_to(DEST)): hashlib.sha256(p.read_bytes()).hexdigest()
        for p in sorted(DEST.rglob("*"))
        if p.is_file() and ".cache" not in p.parts
    }
    if "model.safetensors" not in files:
        raise SystemExit("Prepare the pinned model first: --download")
    manifest = {
        "model_id": MODEL,
        "revision": REVISION,
        "path": str(DEST),
        "dimension": 384,
        "dtype": "float32",
        "max_tokens": 512,
        "query_prefix": "query: ",
        "passage_prefix": "passage: ",
        "pooling": "mean",
        "normalization": "l2",
        "license": "MIT",
        "trust_remote_code": False,
        "files": files,
    }
    Path("models.lock").write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"Pinned local E5: {len(files)} files; manifest models.lock")


if __name__ == "__main__":
    main()
