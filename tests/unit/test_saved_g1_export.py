import json

import pytest

from scripts.export_g1 import write_export


def test_export_needs_new_explicit_directory_and_preserves_previous_work(tmp_path):
    output = tmp_path / "explicit-export"
    payload = {
        "analysis.json": {"id": "synthetic-unit", "claims": []},
        "trace.json": {"id": "trace"},
    }
    result = write_export(output, payload)
    assert result["analysis_id"] == "synthetic-unit" and result["human_decisions"] == 0
    assert json.loads((output / "analysis.json").read_text()) == payload["analysis.json"]
    original = (output / "analysis.json").read_bytes()
    with pytest.raises(FileExistsError):
        write_export(output, {"analysis.json": {"id": "must-not-replace"}})
    assert (output / "analysis.json").read_bytes() == original
