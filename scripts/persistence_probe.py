"""Run create, restart app/worker externally, then run verify in the same DB."""

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from sqlalchemy import select

from app.core import tables as t
from app.core.db import engine
from tests.e2e.test_path import login, result, submit

if sys.argv[1] == "create":
    client, ctx = login("alice")
    _, _, job = submit(client, ctx)
    output = result(client, job)
    print("Persisted synthetic result:", output.id)
    client.close()
else:

    async def verify():
        async with engine.connect() as c:
            rows = (
                (
                    await c.execute(
                        select(t.analyses.c.id).where(t.analyses.c.principal_id == "alice")
                    )
                )
                .scalars()
                .all()
            )
        await engine.dispose()
        return rows

    ids = asyncio.run(verify())
    assert ids, "No persisted results"
    client, _ = login("alice")
    for object_id in ids:
        response = client.get("/api/v1/analyses/" + object_id)
        assert response.status_code == 200, response.text
        assert response.json()["delivery"]["mode"] == "saved_replay"
    print("After restart: readable persisted analyses:", len(ids))
    client.close()
