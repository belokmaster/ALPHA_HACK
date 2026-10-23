"""Versioned, explainable channel rules. No company/event IDs or inferred contract terms."""

import re
from dataclasses import dataclass, field
from datetime import UTC, datetime

from app.ingestion.csv.normalization import assess_time
from contracts.v1 import DeliveryInfo, FeedItem, FeedRelevance, RelevanceReason

POLICY = "company-channels-1"
ORDER = "Сначала сумма весов наблюдаемых признаков, затем дата события (новые раньше), затем заголовок. Баллы не являются вероятностью или размером ущерба."


def channel_for(event):
    if "rates" in event.topics:
        return "rates"
    if "fuel" in event.topics or re.search(r"топлив|бензин|дизел|нефтепродукт", event.title, re.I):
        return "fuel_costs"
    return "unsupported"


def usable_features(profile, request):
    return [
        f
        for f in profile.features
        if f.value is not None
        and f.quality.value not in {"unknown", "conflict"}
        and assess_time(
            request.temporal_mode,
            request.as_of,
            f.available_at,
            f.period,
            f.depends_on_source_versions,
        ).temporal_status
        != "excluded"
    ]


@dataclass
class Match:
    channel: str
    reasons: list = field(default_factory=list)
    selected: list = field(default_factory=list)
    eligible: bool = False
    missing: list = field(default_factory=list)
    mechanism: str = ""
    query: str = ""

    @property
    def score(self):
        return sum(r.weight for r in self.reasons)


def match_event(profile, event, request):
    channel = channel_for(event)
    result = Match(channel)
    features = usable_features(profile, request)
    flags = {f.name: f for f in features if f.value is True and f.quality.value == "verified"}

    def reason(f, code, weight, text, caveat):
        result.reasons.append(
            RelevanceReason(code=code, text=text, weight=weight, feature_ids=[f.id], caveat=caveat)
        )

    if channel == "rates":
        loan = flags.get("CL_PRODUCT_LOA_MARK")
        lease = flags.get("CL_PRODUCT_LEA_MARK")
        if loan:
            result.selected.append(loan)
            reason(
                loan,
                "observed_credit_product",
                80,
                "В профиле есть кредитные продукты: событие о ставках требует проверки условий финансирования.",
                "Наличие продукта не раскрывает остаток, тип ставки, график и срок пересмотра.",
            )
        if lease:
            reason(
                lease,
                "observed_leasing_product",
                10,
                "В профиле есть лизинг; условия и чувствительность к ставке неизвестны.",
                "Флаг лизинга не доказывает плавающую ставку или изменение платежа.",
            )
        result.eligible = loan is not None
        result.missing = [
            "contract_rate_type",
            "repricing_date",
            "refinancing_plans",
            "loan_balance",
        ]
        if not result.eligible:
            result.missing.append("financing_exposure")
        result.mechanism = "В профиле наблюдаются кредитные продукты. Связь со ставкой требует проверки договора и планов финансирования; рост действующего платежа из флага не следует."
        result.query = "ключевая ставка прогноз конец года"
    elif channel == "fuel_costs":
        industry = next(
            (
                f
                for f in features
                if f.name == "MAINOKVED_CODE"
                and re.fullmatch(r"(?:49|50|51|52|53)(?:\.\d+)*", str(f.value))
            ),
            None,
        )
        if industry:
            result.selected.append(industry)
            reason(
                industry,
                "transport_industry_observed",
                60,
                "Основной ОКВЭД относится к транспорту и сопутствующим услугам: цены на топливо могут быть связаны с себестоимостью перевозок.",
                "Отрасль не доказывает наличие автопарка, закупок топлива или долю затрат; связь условна.",
            )
        result.eligible = industry is not None
        result.missing = ["fuel_cost_share", "fuel_contract_terms", "fleet_or_transport_purchases"]
        if not result.eligible:
            result.missing.append("transport_exposure")
        result.mechanism = "Основной вид деятельности связан с перевозками. Топливный канал условно значим через собственное потребление или закупку транспортных услуг; их наличие и доля в расходах пока не установлены."
        result.query = "бензин дизель топливо цены инфляционный риск"
    else:
        result.missing = ["supported_economic_channel"]
    return result


def rank_events(profile, events, context, request):
    ranked = []
    for event in events:
        if event.event_at > request.as_of:
            continue
        temporal = assess_time(
            request.temporal_mode,
            request.as_of,
            event.available_at,
            None,
            [event.source_version_id],
        )
        if temporal.temporal_status == "excluded":
            continue
        if event.domain == "synthetic":
            ranked.append((0, event, None))
            continue
        match = match_event(profile, event, request)
        if match.score:
            ranked.append((match.score, event, match))
    ranked.sort(
        key=lambda row: (-row[0], -row[1].event_at.timestamp(), row[1].title.casefold(), row[1].id)
    )
    return [
        FeedItem(
            event=event,
            context_version=context.version,
            reason_codes=[r.code for r in match.reasons] if match else ["synthetic_scenario"],
            feature_ids=list(dict.fromkeys(fid for r in match.reasons for fid in r.feature_ids))
            if match
            else [],
            relevance=FeedRelevance(
                policy_version=POLICY,
                channel=match.channel,
                score=score,
                reasons=match.reasons,
                order_rule=ORDER,
            )
            if match
            else None,
            delivery=DeliveryInfo(
                mode="fixture" if event.domain == "synthetic" else "evidence_only",
                generated_at=datetime.now(UTC),
            ),
        )
        for score, event, match in ranked
    ]
