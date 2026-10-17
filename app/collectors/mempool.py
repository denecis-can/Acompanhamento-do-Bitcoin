"""mempool.space: taxas, mempool, hashrate, ajuste de dificuldade e halving."""

from __future__ import annotations

import time

from app.collectors.base import BaseCollector, CollectorError, CollectorResult

BASE = "https://mempool.space/api"
HALVING_INTERVAL = 210_000
EH = 1e18


def halving(height: int, avg_block_ms: float | None, now: float) -> dict:
    epoch = height // HALVING_INTERVAL
    next_height = (epoch + 1) * HALVING_INTERVAL
    remaining = next_height - height
    block_s = (avg_block_ms or 600_000) / 1000
    return {
        "next_height": next_height,
        "blocks_remaining": remaining,
        "estimated_ts": int(now + remaining * block_s),
        "subsidy_btc": 50 / 2**epoch,
        "next_subsidy_btc": 50 / 2 ** (epoch + 1),
    }


def parse(fees: dict, mempool: dict, diff: dict, hashrate: dict, height: int, now: float) -> dict:
    if "fastestFee" not in fees or "count" not in mempool:
        raise CollectorError("mempool.space: resposta inesperada")
    return {
        "height": height,
        "fees_sat_vb": {
            "fast": fees["fastestFee"],
            "half_hour": fees["halfHourFee"],
            "hour": fees["hourFee"],
            "economy": fees["economyFee"],
        },
        "mempool_tx": mempool["count"],
        "mempool_vmb": mempool["vsize"] / 1e6,
        "mempool_fees_btc": mempool["total_fee"] / 1e8,
        "hashrate_ehs": hashrate["currentHashrate"] / EH,
        "difficulty": hashrate["currentDifficulty"],
        "difficulty_adjustment": {
            "progress_pct": diff["progressPercent"],
            "estimated_change_pct": diff["difficultyChange"],
            "previous_change_pct": diff["previousRetarget"],
            "remaining_blocks": diff["remainingBlocks"],
            "estimated_ts": int(diff["estimatedRetargetDate"] / 1000),
            "avg_block_s": diff["timeAvg"] / 1000,
        },
        "halving": halving(height, diff.get("timeAvg"), now),
    }


class MempoolCollector(BaseCollector):
    name = "mempool"
    source = "mempool.space"

    async def collect(self) -> CollectorResult:
        g = self.http.get_json
        fees = await g(f"{BASE}/v1/fees/recommended")
        mempool = await g(f"{BASE}/mempool")
        diff = await g(f"{BASE}/v1/difficulty-adjustment")
        hashrate = await g(f"{BASE}/v1/mining/hashrate/3m")
        height = int(await g(f"{BASE}/blocks/tip/height"))  # texto puro, mas é JSON válido
        data = parse(fees, mempool, diff, hashrate, height, time.time())
        series = [("mempool.hashrate_ehs", int(h["timestamp"]), h["avgHashrate"] / EH) for h in hashrate.get("hashrates", [])]
        return CollectorResult(data=data, series=series)
