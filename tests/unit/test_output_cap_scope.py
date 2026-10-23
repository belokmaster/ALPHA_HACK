"""Operator acceptance is global; ordinary model and endpoint allowlists remain."""

import pytest

from app.budgets.service import BudgetLedger
from app.core.errors import AppError
from app.llm.config import ALLOWED_BASE_URLS, ALLOWED_MODEL_IDS, LLMConfig
from app.llm.gateway import LLMGateway
from app.llm.runtime import RuntimeConfig, load_runtime


@pytest.mark.parametrize('url', sorted(ALLOWED_BASE_URLS))
@pytest.mark.parametrize('model', sorted(ALLOWED_MODEL_IDS))
def test_accepted_cap_does_not_require_separate_model_permissions(url, model):
    raw = load_runtime().model_dump()
    for stage in raw['stages'].values():
        stage['model_id'] = model
    gateway = LLMGateway(BudgetLedger(None), config=LLMConfig(
        base_url=url, billable_output_cap_confirmed=True
    ), runtime=RuntimeConfig.model_validate(raw))
    gateway.require_output_cap()


def test_missing_operator_acceptance_still_blocks_without_session():
    gateway = LLMGateway(BudgetLedger(None))
    with pytest.raises(AppError):
        gateway.require_output_cap()
