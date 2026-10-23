# Third-party notices

Actual locked Python distributions, read from installed metadata. Full license texts remain in each distribution dist-info/licenses.

| Component | Version | License metadata |
|---|---|---|
| alembic | 1.20.0 | MIT |
| annotated-doc | 0.0.5 | MIT |
| annotated-types | 0.8.0 | MIT |
| anyio | 4.15.1 | MIT |
| asyncpg | 0.32.0 | Apache-2.0 |
| certifi | 2026.10.19 | MPL-2.0 |
| click | 8.5.0 | BSD-3-Clause |
| fastapi | 0.156.7 | MIT |
| greenlet | 3.5.6 | MIT AND PSF-2.0 |
| h11 | 0.16.0 | MIT |
| httpcore | 1.0.9 | BSD-3-Clause |
| httpx | 0.28.1 | BSD-3-Clause |
| idna | 3.20 | BSD-3-Clause |
| iniconfig | 2.3.1 | MIT |
| Jinja2 | 3.1.6 | License :: OSI Approved :: BSD License |
| librt | 0.16.0 | MIT |
| Mako | 1.4.3 | MIT |
| MarkupSafe | 3.0.4 | BSD-3-Clause |
| mypy | 1.20.2 | MIT |
| mypy_extensions | 1.1.0 | MIT |
| opentelemetry-api | 1.45.1 | Apache-2.0 |
| packaging | 26.3 | Apache-2.0 OR BSD-2-Clause |
| pathspec | 1.1.1 | License :: OSI Approved :: Mozilla Public License 2.0 (MPL 2.0) |
| pluggy | 1.7.0 | MIT |
| pydantic | 2.14.1 | MIT |
| pydantic-settings | 2.16.1 | MIT |
| pydantic_core | 2.50.1 | MIT |
| Pygments | 2.21.0 | BSD-2-Clause |
| pytest | 9.1.1 | MIT |
| pytest-asyncio | 1.4.0 | Apache-2.0 |
| python-dotenv | 1.2.4 | BSD-3-Clause |
| ruff | 0.17.1 | MIT |
| SQLAlchemy | 2.1.5 | MIT |
| starlette | 1.9.0 | BSD-3-Clause |
| typing-inspection | 0.4.4 | MIT |
| typing_extensions | 4.16.0 | PSF-2.0 |
| uvicorn | 0.54.0 | BSD-3-Clause |

Python 3.12.14: PSF License. uv 0.13.4: MIT/Apache-2.0. PostgreSQL 17.7: PostgreSQL License.
Container base: python:3.12.14-slim-bookworm (Debian system components retain their individual copyright/license files).
Docker/Compose is required infrastructure; this run uses host Podman 5.8.7 compatibility and Compose 5.6.0.
Synthetic fixtures and demo bank session are created for this project; no real bank claims.
Case authors: Changellenge and АО Альфа-Банк. Raw CSV/PDF are external local inputs, not redistributed here.
No HTMX, E5 weights, OCR model, OpenAI SDK or generative model weights are bundled.
Configured organizer gateway/model IDs are documented but never called by bootstrap/tests.

Optional local ML runner (not the default app startup): CPU torch 2.7.1+cpu,
sentence-transformers 4.1.0, transformers 4.51.3, NumPy 2.2.6, pypdfium2 4.30.0,
Pillow 11.1.0, pytesseract 0.3.13, PySocks 1.7.1; all transitive wheels/hashes in uv.lock.
Full license texts remain in the installed distributions. Tesseract 5.3.0 and
rus/eng traineddata Debian 1:4.1.0-2 are installed only in src/Dockerfile.ml;
OCR runtime and traineddata SHA-256 are retained in local page manifests.
Local E5: intfloat/multilingual-e5-small, MIT model card, revision
614241f622f53c4eeff9890bdc4f31cfecc418b3; model/tokenizer file hashes in models.lock.
Weights are prepared explicitly into ignored models/, not bundled in Git or the image.
Case PDF snippets in config/bank_corpus.json are limited source excerpts for verification;
raw bank PDFs are not redistributed. Agent page checks are not human review labels.

Russian Trusted CA public root and intermediate certificates supplied by the user are
bundled in config/certs for Rosstat-only TLS trust. Issuer: The Ministry of Digital
Development and Communications. Provenance, fingerprints, validity and update procedure:
[config/certs/README.md](config/certs/README.md). These are public certificates, not private keys;
no separate redistribution license has been established. No OS/global trust installation.
