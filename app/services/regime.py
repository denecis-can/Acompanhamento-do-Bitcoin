"""Temperatura do mercado: um resumo descritivo de quão esticados estão posicionamento,
sentimento, valuation e fluxos, comparados a faixas de referência fixas.

Não é sinal de compra ou venda. O método é deliberadamente simples e todo exposto no
/api/summary (valor, âncoras e contribuição de cada componente):

1. cada componente vira uma nota de −1 (frio) a +1 (quente) por interpolação linear entre
   três âncoras: frio → −1, neutro → 0, quente → +1 (valores além das pontas são limitados);
2. o score é a média simples das notas disponíveis × 100 (pesos iguais);
3. sem pelo menos MIN_COMPONENTS componentes, não há score.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

MIN_COMPONENTS = 4

DESCRIPTION = (
    "Média de componentes de posicionamento, sentimento, valuation e fluxos, cada um "
    "convertido para uma nota de −1 (frio) a +1 (quente) por faixas fixas. "
    "Descreve o estado do mercado; não é recomendação de compra ou venda."
)

BANDS = ((-60, "Muito frio"), (-20, "Frio"), (20, "Neutro"), (60, "Aquecido"), (float("inf"), "Muito aquecido"))


@dataclass(frozen=True)
class Component:
    key: str
    label: str
    group: str
    unit: str
    cold: float
    neutral: float
    hot: float
    why: str


COMPONENTS = (
    Component("funding", "Funding ponderado (anualizado)", "Posicionamento", "%", -5, 10, 30,
              "Funding alto: comprados alavancados pagando caro para manter posição."),
    Component("basis", "Basis do futuro ~3 meses (anualizado)", "Posicionamento", "%", 0, 7, 15,
              "Prêmio do futuro sobre o spot: demanda por exposição comprada com prazo."),
    Component("skew", "Skew 25Δ ~30 dias (put − call)", "Posicionamento", "pts", 8, 0, -5,
              "Put mais cara que call = procura por proteção contra queda (frio)."),
    Component("fear_greed", "Fear & Greed", "Sentimento", "", 20, 50, 80,
              "Índice de sentimento da Alternative.me (0 a 100)."),
    Component("mayer", "Mayer Multiple", "Valuation", "×", 0.8, 1.1, 2.0,
              "Preço dividido pela média de 200 dias."),
    Component("mvrv", "MVRV", "Valuation", "×", 1.0, 1.8, 3.2,
              "Valor de mercado dividido pelo valor realizado (custo médio on-chain)."),
    Component("etf_5d", "Fluxo dos ETFs (5 pregões)", "Fluxos", "US$ mi", -1000, 0, 1000,
              "Entradas líquidas somadas dos ETFs à vista nos EUA."),
    Component("stables_30d", "Oferta de stablecoins (30 dias)", "Fluxos", "%", -1, 0.5, 3,
              "Crescimento da oferta em dólar: liquidez entrando no mercado cripto."),
)


def normalize(value: float, cold: float, neutral: float, hot: float) -> float:
    """Interpolação linear por partes para [−1, +1]; funciona com âncoras crescentes ou decrescentes."""
    if (value - neutral) * (hot - neutral) >= 0:
        t = (value - neutral) / (hot - neutral)
    else:
        t = -(value - neutral) / (cold - neutral)
    return max(-1.0, min(1.0, t))


def label_for(score: float) -> str:
    return next(name for limit, name in BANDS if score <= limit)


def extract_inputs(summary: dict[str, Any]) -> dict[str, float | None]:
    """Lê os valores de cada componente do /api/summary já montado."""

    def get(*path: str) -> Any:
        node: Any = summary
        for key in path:
            if not isinstance(node, dict):
                return None
            node = node.get(key)
        return node

    return {
        "funding": get("derivatives", "weighted_funding", "annualized_pct"),
        "basis": get("basis", "headline", "annualized_pct"),
        "skew": get("options", "ref_30d", "skew_25d"),
        "fear_greed": get("sentiment", "value"),
        "mayer": get("trend", "mayer_multiple"),
        "mvrv": get("onchain", "valuation", "mvrv"),
        "etf_5d": get("etf", "sum_5d"),
        "stables_30d": get("stablecoins", "total", "change_30d_pct"),
    }


def compute_regime(inputs: dict[str, float | None]) -> dict[str, Any]:
    components, missing = [], []
    for c in COMPONENTS:
        value = inputs.get(c.key)
        if value is None:
            missing.append(c.label)
            continue
        components.append(
            {
                "key": c.key,
                "label": c.label,
                "group": c.group,
                "unit": c.unit,
                "value": value,
                "anchors": {"cold": c.cold, "neutral": c.neutral, "hot": c.hot},
                "contribution": normalize(value, c.cold, c.neutral, c.hot),
                "why": c.why,
            }
        )
    score = None
    if len(components) >= MIN_COMPONENTS:
        score = sum(x["contribution"] for x in components) / len(components) * 100
    return {
        "score": score,
        "label": label_for(score) if score is not None else None,
        "components": components,
        "missing": missing,
        "available": len(components),
        "total": len(COMPONENTS),
        "min_components": MIN_COMPONENTS,
        "bands": [{"up_to": None if limit == float("inf") else limit, "label": name} for limit, name in BANDS],
        "method": DESCRIPTION,
    }
