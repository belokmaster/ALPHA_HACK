"""Explicit test transport. No sockets, keys or real model; never used as live fallback."""

import json

import httpx


def transport(response_mode: str) -> httpx.MockTransport:
    async def respond(request: httpx.Request) -> httpx.Response:
        if response_mode == "timeout":
            raise httpx.ReadTimeout("Controlled unknown outcome", request=request)
        body = json.loads(request.content)
        prompt = body["messages"][1]["content"]
        if body.get("model") and '"channels"' in body["messages"][0]["content"]:
            pack = json.loads(prompt.split("Данные: ", 1)[1])
            value = {
                "channels": [pack["channel"]],
                "required_fields": [
                    x for x in pack["unknown"] if x in {"contract_rate_type", "fuel_cost_share"}
                ],
                "bank_query": "ключевая ставка прогноз"
                if pack["channel"] == "rates"
                else "топливо бензин инфляция",
                "refined_bank_query": "новый прогноз ставки конец года"
                if pack["channel"] == "rates"
                else "топливо инфляционный риск обновление",
                "if_missing": [],
            }
        elif "proposals=" in prompt:
            proposed = json.loads(prompt.split("\nproposals=", 1)[1])
            value = {
                "verdicts": [
                    {
                        "index": p["index"],
                        "status": "unsupported"
                        if response_mode == "reject_claims"
                        else "conditional",
                        "reason": "Controlled verifier fixture, not a financial quality evaluation",
                    }
                    for p in proposed
                ]
            }
        else:
            pack = json.loads(prompt.split("Данные: ", 1)[1])
            # An explicitly disclosed fixture, parameterized by the actual evidence IDs.
            # The production engine has neither these words nor company-specific branches.
            fixed = any(
                "фикс" in a["value"].casefold() or "fixed" in a["value"].casefold()
                for a in pack["assumptions"]
            )
            fuel = pack.get("channel") == "fuel_costs"
            ceo = pack.get("role") == "ceo"
            if fuel:
                risk = "Если компания покупает топливо или перевозки и будущий рост цен передаётся в тарифы, расходы могут превысить бюджет; доля затрат пока неизвестна."
                opportunity = "Если снижение закупочных цен передаётся в тарифы поставщиков, может появиться возможность пересмотреть бюджет перевозок; экономия не подтверждена."
                action = (
                    "До изменения коммерческих планов поручите проверить устойчивость логистики и варианты поставок."
                    if ceo
                    else "До изменения бюджета запросите долю топливных и транспортных расходов и порядок индексации договоров."
                )
                condition = (
                    "Подтвердить фактические закупки топлива или перевозок и условия передачи цен"
                )
                alternative = "Если транспорт закупается по фиксированным тарифам, прямой эффект может быть ограничен"
            else:
                risk = (
                    "При заявленной фиксации прямое изменение действующего платежа не обосновано; новое финансирование требует отдельной проверки."
                    if fixed
                    else "Если предстоит новое финансирование или пересмотр плавающей ставки, стоимость заимствований может отличаться от допущений бюджета."
                )
                opportunity = "Если будущие условия финансирования улучшатся, можно сравнить варианты нового кредита; снижение расходов действующего договора не доказано."
                action = (
                    "До изменения инвестиционного плана поручите финансовой команде проверить сценарии финансирования и устойчивость обязательств."
                    if ceo
                    else "До корректировки финансового плана сопоставьте сроки пересмотра договоров с допущениями бюджета и уточните условия у казначейства."
                )
                condition = (
                    "Подтвердить тип ставки, сроки пересмотра и потребность в новом финансировании"
                )
                alternative = "При отсутствии новых заимствований и пересмотра условий прямое влияние может быть ограничено"
            ids = [e["id"] for e in pack["event"][:1]] + [
                e["id"] for e in pack.get("bank_background", [])[:1]
            ]
            value = {
                "proposals": [
                    {
                        "text": text,
                        "section": section,
                        "impact_kind": impact,
                        "evidence_ids": ids,
                        "feature_ids": [f["id"] for f in pack["features"][:1]],
                        "conditions": [condition],
                        "alternatives": [alternative],
                    }
                    for text, section, impact in [
                        (risk, "consequences", "risk"),
                        (opportunity, "consequences", "opportunity"),
                        (action, "actions", "neutral"),
                    ]
                ]
            }
        envelope = {
            "model": body["model"],
            "usage": {"prompt_tokens": 100, "completion_tokens": 100, "total_tokens": 200},
            "choices": [
                {
                    "finish_reason": "stop",
                    "message": {
                        "content": "not JSON"
                        if response_mode == "invalid_json"
                        else json.dumps(value)
                    },
                }
            ],
        }
        if response_mode == "missing_usage":
            del envelope["usage"]
        return httpx.Response(200, json=envelope)

    return httpx.MockTransport(respond)
