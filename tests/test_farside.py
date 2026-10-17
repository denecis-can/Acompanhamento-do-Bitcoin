"""Leitor da tabela da Farside (página real gravada em 17/10/2026), em HTML e CSV."""

import re
from pathlib import Path

import pytest

from app.collectors.base import CollectorError
from app.collectors.farside import parse, parse_csv, parse_date, parse_number

HTML = (Path(__file__).parent / "fixtures" / "farside_btc.html").read_text(encoding="utf-8")


@pytest.mark.parametrize(
    "text, expected",
    [("54.8", 54.8), ("(10.9)", -10.9), ("1,119.9", 1119.9), ("(1,113.7)", -1113.7), ("-", None), ("", None), ("n/a", None)],
)
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_parse_date():
    assert parse_date("28 Sep 2026") == "2026-09-28"
    assert parse_date("2026-09-28") == "2026-09-28"
    assert parse_date("Total") is None
    assert parse_date("28 Xyz 2026") is None


def test_parse_real_table():
    d = parse(HTML)

    assert d["last_day"]["date"] == "2026-10-16"
    assert d["last_day"]["total"] == -82.4
    assert d["last_day"]["by_ticker"]["IBIT"] == -71.8
    assert len(d["days"]) == 15 and d["rejected_rows"] == 0
    # 5 últimos pregões: 175.7 − 261.4 + 5.1 − 82.4 − 65.4
    assert d["sum_5d"] == pytest.approx(-228.4)
    assert d["cumulative_total"] == 56_949
    tickers = [t["ticker"] for t in d["by_ticker"]]
    assert tickers[:3] == ["IBIT", "FBTC", "BITB"] and "GBTC" in tickers and "BTC" in tickers
    ibit = d["by_ticker"][0]
    assert ibit["issuer"] == "BlackRock" and ibit["cumulative"] == 65_568


def test_pending_day_is_not_counted():
    # troca todos os números da linha de 16/10 por "-": dia listado, mas ainda sem dados
    start = HTML.index("16 Oct 2026")
    end = HTML.index("</tr>", start)
    blank = re.sub(r">\(?[\d.,]+\)?<", ">-<", HTML[start:end])
    d = parse(HTML[:start] + blank + HTML[end:])
    assert d["last_day"]["date"] == "2026-10-15"
    assert d["pending_dates"] == ["2026-10-16"]


def test_row_with_inconsistent_total_is_rejected():
    # adultera o total de 16/10 (−82.4 → 999.0): a soma dos ETFs não bate mais
    i = HTML.index("16 Oct 2026")
    j = HTML.index("(82.4)", i)
    d = parse(HTML[:j] + "999.0" + HTML[j + len("(82.4)"):])
    assert d["rejected_rows"] == 1
    assert d["last_day"]["date"] == "2026-10-15"


def test_cloudflare_page_saved_by_mistake_is_detected():
    page = "<html><head><title>Just a moment...</title></head><body>cf-chl</body></html>"
    with pytest.raises(CollectorError, match="Cloudflare"):
        parse(page)


def test_layout_change_fails_clearly():
    with pytest.raises(CollectorError, match="tabela"):
        parse("<html><body><table class='other'><tr><td>1</td></tr></table></body></html>")
    with pytest.raises(CollectorError, match="IBIT"):
        parse('<table class="etf"><tr><th>x</th><th>Total</th></tr></table>')


CSV_ROWS = [
    ["Date", "IBIT", "FBTC", "GBTC", "Total"],
    ["12 Oct 2026", "(65.4)", "0.0", "0.0", "(65.4)"],
    ["13 Oct 2026", "65.9", "103.1", "0.0", "169.0"],
    ["14 Oct 2026", "(110.9)", "(152.7)", "0.0", "(263.6)"],
    ["15 Oct 2026", "16.8", "0.0", "0.0", "16.8"],
    ["2026-10-16", "(71.8)", "(10.6)", "0.0", "(82.4)"],
    ["19 Oct 2026", "-", "-", "-", "-"],
    ["Total", "65,568", "10,465", "(27,944)", "48,089"],
]


@pytest.mark.parametrize("delimiter", [",", ";"])
def test_parse_csv(delimiter):
    def cell(c):
        return f'"{c}"' if delimiter in c else c

    text = "\n".join(delimiter.join(cell(c) for c in r) for r in CSV_ROWS)
    d = parse_csv(text)
    assert d["last_day"] == {"date": "2026-10-16", "total": -82.4, "by_ticker": {"IBIT": -71.8, "FBTC": -10.6, "GBTC": 0.0}}
    assert d["sum_5d"] == pytest.approx(-225.6)
    assert d["pending_dates"] == ["2026-10-19"]
    assert d["cumulative_total"] == 48_089


def test_parse_csv_empty():
    with pytest.raises(CollectorError):
        parse_csv("")
