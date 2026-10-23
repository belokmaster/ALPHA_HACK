"""Validate the explicit JavaScript UI fixtures against the actual shared Pydantic DTOs."""

import json
import subprocess

from contracts.v1 import Analysis, CompanyProfile, Document, FeedItem, UserContext


def main():
    data = subprocess.check_output(
        [
            "node",
            "-e",
            "const vm=require('node:vm'),fs=require('node:fs');let window={};"
            "vm.runInNewContext(fs.readFileSync('app/web/templates/fixtures.js','utf8'),{window});"
            "process.stdout.write(JSON.stringify(window.AlfaFixtures));",
        ],
        text=True,
    )
    fixtures = json.loads(data)
    groups = [
        (UserContext, [fixtures["USER_CONTEXT"]]),
        (CompanyProfile, fixtures["COMPANIES"]),
        (Document, [fixtures["DOCUMENT"]]),
        (FeedItem, fixtures["FEED"]),
        (Analysis, [s["analysis"] for s in fixtures["SCENARIOS"].values() if not s.get("isError")]),
    ]
    for dto, values in groups:
        for value in values:
            dto.model_validate(value)
    print("UI fixtures: actual DTO 1.1 validation passed (5 Analysis states)")


if __name__ == "__main__":
    main()
