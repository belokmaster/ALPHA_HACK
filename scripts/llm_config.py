"""Validate/print effective non-secret runtime and maximum reserves. No DB/key/HTTP."""

import argparse
import json
from decimal import Decimal
from pathlib import Path

from app.core.config import Settings
from app.core.errors import AppError
from app.llm.config import ALLOWED_BASE_URLS
from app.llm.factory import load_tariffs
from app.llm.runtime import RuntimeConfig, load_runtime, quote_stages
from app.llm.session import risk_reserve


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--mode", choices=["adaptive", "two_call_smoke"], default="adaptive")
    parser.add_argument("--schema", action="store_true")
    args = parser.parse_args()
    if args.schema:
        print(json.dumps(RuntimeConfig.model_json_schema(), ensure_ascii=False, indent=2))
        return
    cfg = Settings()
    runtime = load_runtime(cfg)
    if cfg.llm_base_url not in ALLOWED_BASE_URLS:
        parser.error("Endpoint outside allowlist")
    quotes = quote_stages(runtime, args.mode, load_tariffs(Path(cfg.llm_tariff_file)))
    maximum = sum((Decimal(q["maximum_reserve_rub"]) for q in quotes), Decimal("0"))
    tariff_estimate = maximum
    if cfg.llm_live_session_id:
        maximum = sum((risk_reserve(Decimal(q["maximum_reserve_rub"])) for q in quotes), Decimal("0"))
    print(
        json.dumps(
            {
                "runtime_file": cfg.llm_runtime_file,
                "analysis_mode": cfg.analysis_mode,
                "quote_scope": "live_tariff_file_not_controlled_fixture_rates",
                "tariff_file": cfg.llm_tariff_file,
                "runtime_sha256": runtime.sha256,
                "runtime": runtime.model_dump(mode="json"),
                "mode": args.mode,
                "base_url": cfg.llm_base_url,
                "stage_reserves": quotes,
                "maximum_reserve_rub": str(maximum),
                "tariff_envelope_rub": str(sum(
                    (Decimal(q["nominal_tariff_rub"]) for q in quotes), Decimal("0")
                )),
                "estimated_cost_envelope_rub": str(tariff_estimate),
                "cost_estimate_confirmed_by_owner": all(
                    q["estimate_confirmation"] == "owner_accepted" for q in quotes
                ),
                "session_id": cfg.llm_live_session_id or None,
                "risk_margin": "max(1 RUB, 4x calibrated estimate); not a verified provider output cap"
                if cfg.llm_live_session_id else None,
                "within_run_budget": maximum <= runtime.limits.budget_rub,
                "billable_output_cap_confirmed": cfg.llm_billable_output_cap_confirmed,
                "live_authorized": False,
            },
            ensure_ascii=False,
            indent=2,
        )
    )
    if maximum > runtime.limits.budget_rub:
        raise SystemExit("Maximum reserve exceeds runtime budget")


if __name__ == "__main__":
    try:
        main()
    except AppError as exc:
        print(json.dumps({"error": exc.code.value, "message": exc.message}))
        raise SystemExit(1) from None
