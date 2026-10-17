"""Regra de status exibida em cada card: ok / atrasado / erro / pendente."""

from __future__ import annotations

from typing import Any

OK, STALE, ERROR, PENDING = "ok", "atrasado", "erro", "pendente"


def compute_status(entry: dict[str, Any] | None, stale_after: float, now: float) -> str:
    """
    - pendente: o coletor ainda não rodou
    - ok:       última tentativa bem-sucedida e dado dentro da janela de validade
    - erro:     nunca houve dado válido, ou a última tentativa falhou e o cache já venceu
    - atrasado: há dado em cache, mas vencido ou servido após falha recente
    """
    if entry is None:
        return PENDING
    fetched_at = entry.get("fetched_at")
    if fetched_at is None:
        return ERROR
    fresh = (now - fetched_at) <= stale_after
    if entry.get("ok") and fresh:
        return OK
    if not entry.get("ok") and not fresh:
        return ERROR
    return STALE


def is_usable(status: str) -> bool:
    """Dados que podem entrar em cálculos derivados (ok ou atrasado com cache)."""
    return status in (OK, STALE)
