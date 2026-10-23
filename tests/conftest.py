import csv
import os

import pytest
from sqlalchemy.ext.asyncio import create_async_engine


@pytest.fixture
async def db():
    url = os.environ.get("TEST_DATABASE_URL", "")
    if not url or url.rsplit("/", 1)[-1] != "alfa_test":
        pytest.fail("Explicit isolated TEST_DATABASE_URL ending /alfa_test required")
    engine = create_async_engine(url)
    yield engine
    await engine.dispose()


@pytest.fixture
def case_csv(tmp_path):
    from app.ingestion.csv.audit import FILES, SCHEMAS

    data = {
        "companies": [
            {
                "SYNTHETIC_U": cid,
                "CL_TYPE": "CLIENT",
                "GROUP_ID": "G1",
                "REVENUE_COMPANY": "1000",
                "CL_PRODUCT_LOA_MARK": "1.0",
            }
            for cid in ("U1", "U2", "Uhidden", "Uq")
        ]
        + [{"SYNTHETIC_U": "Uq", "CL_TYPE": "PROSPECT"}],
        "links": [
            {"SYNTHETIC_U": cid, "SYNTHETIC_F": "F1", "CITY_NAME": "Москва"}
            for cid in ("U1", "U2", "Uhidden")
        ],
        "payments": [
            {"PAYER": "U1", "BENEFICIARY": target, "PAY_AMT": amount, "PAY_CNT": "1"}
            for target, amount in (
                ("U1", "100"),
                ("U2", "10"),
                ("U2", "10"),
                ("Uhidden", "20"),
                ("Uhidden", "30"),
                ("Ustub", "5"),
            )
        ],
    }
    for kind, rows in data.items():
        with (tmp_path / FILES[kind]).open("w", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=SCHEMAS[kind], delimiter=";")
            writer.writeheader()
            writer.writerows(rows)
    return tmp_path
