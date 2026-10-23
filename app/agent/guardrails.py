"""Fail closed: foreign refs/numeric effects/bank impersonation are removed.

Unlike the PR's original guardrails, adding a caveat never legitimizes an invented
number, missing premise or negative verifier verdict. No automatic repair.
"""

import re
from datetime import date


def candidate_rejection(
    p, allowed_evidence, features, required_event_ids=None, *, channel=None, assumptions=()
):
    if not p.evidence_ids or not set(p.evidence_ids) <= allowed_evidence:
        return "missing_or_foreign_evidence"
    if required_event_ids is not None and not set(p.evidence_ids) & required_event_ids:
        return "event_not_cited"
    if not set(p.feature_ids) <= {f.id for f in features}:
        return "foreign_feature"
    # Product presence + user-declared fixed terms do not establish an effect
    # on existing debt. Keep contract checks, not positive/negative rate effects.
    if (
        channel == "rates"
        and p.section == "consequences"
        and any(a.name == "contract_rate_type" and a.value == "fixed" for a in assumptions)
    ):
        until = next((a.value for a in assumptions if a.name == "fixed_until"), None)
        try:
            date.fromisoformat(until or "")
        except ValueError:
            return "fixed_contract_effect_not_established"
        if (p.effect_window != "after_fixed_until"
                or not any("после окончания фиксации" in c.casefold() for c in p.conditions)):
            return "fixed_contract_effect_not_established"
    text = " ".join([p.text, *p.conditions, *p.alternatives])
    if re.search(r"\d|%|₽", text):
        return "numeric_effect_or_unapproved_number"
    if re.search(r"по (?:мнению|прогнозу) банка|банк (?:считает|ожидает)|гарантир", text, re.I):
        return "bank_attribution_or_guarantee"
    return None


def candidates(
    proposals, event_ids, features, assumptions, *, required_event_ids=None, channel=None
):
    accepted = []
    for i, p in enumerate(proposals):
        if candidate_rejection(
            p, event_ids, features, required_event_ids, channel=channel, assumptions=assumptions
        ):
            continue
        accepted.append((i, p))
    return accepted


def verified_proposals(proposed, verdicts):
    indices = [v.index for v in verdicts]
    if not set(indices) <= {i for i, _ in proposed}:
        return []
    return [
        (i, p)
        for i, p in proposed
        if indices.count(i) == 1
        and next(v for v in verdicts if v.index == i).status == "conditional"
    ]
