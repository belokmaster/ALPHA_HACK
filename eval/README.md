# M01–M03 evaluation status

`python -m eval.validate` validates the exposed development smoke schema (5 specs,
11 queries). `python -m eval.validate --acceptance` exits 2 while the planned 24-event /
24-query independent gold and human review are absent; see docs/gold/guide.md.

`python -m eval.run` requires explicit isolated TEST_DATABASE_URL ending /alfa_test
and prepared local E5. It compares nine fixed document-coverage probes with FTS,
exact pgvector and actual HNSW plans. It exits 2 because technical coverage is not a
gold quality gate. No test queries are used to tune retrieval. A nonempty no-answer
candidate list is allowed; downstream bank attribution is not evaluated before M04.

`python -m scripts.verify_ml_corpus` runs real cached OCR, local embeddings, atomic
publication, rollback/repeat and the same comparison; exits 0 for technical checks.
See docs/handoffs/M01-M03.md for dependencies, preparation and measured results.
