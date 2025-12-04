from pathlib import Path

import pytest

pytest.importorskip("bs4")
from pathlib import Path

from bs4 import BeautifulSoup


def _load_dom() -> BeautifulSoup:
    repo_root = Path(__file__).resolve().parents[1]
    html = (repo_root / "static" / "index.html").read_text(encoding="utf-8")
    return BeautifulSoup(html, "html.parser")


def test_title_and_header_text():
    dom = _load_dom()

    assert dom.title is not None
    assert dom.title.string.strip() == "Trading Suite"

    header = dom.find("h1")
    assert header is not None
    assert "Trading Suite" in header.get_text(strip=True)


def test_assets_and_tabs_present():
    dom = _load_dom()

    links = [link["href"] for link in dom.find_all("link", href=True)]
    assert "/static/css/theme.css" in links

    scripts = [script["src"] for script in dom.find_all("script", src=True)]
    assert "/static/js/main.js" in scripts

    tabs = [btn.get_text(strip=True) for btn in dom.select(".tab-bar button")]
    assert any("Mean Reversion" in text for text in tabs)
    assert any("Bollinger" in text for text in tabs)
    assert any("Trend" in text for text in tabs)
    assert any("Relative Strength" in text for text in tabs)


def test_mean_and_bollinger_forms_exist():
    dom = _load_dom()

    assert dom.find("form", id="configForm") is not None
    assert dom.find("form", id="manualTradeForm") is not None
    assert dom.find("form", id="bollConfigForm") is not None
    assert dom.find("form", id="trendConfigForm") is not None
    assert dom.find("form", id="rsConfigForm") is not None


def test_trading_tab_and_form_present():
    dom = _load_dom()

    tab = dom.find("div", id="tab-trading")
    assert tab is not None

    # Environment/account selectors
    assert tab.find("select", id="tradingEnv") is not None
    assert tab.find("select", id="tradingAccount") is not None

    # Balance grid and manual order form
    assert tab.find("div", id="tradingBalances") is not None

    form = tab.find("form", id="tradingOrderForm")
    assert form is not None
    assert form.find("input", id="tradingSymbol") is not None
    assert form.find("select", id="tradingSide") is not None
    assert form.find("input", id="tradingQty") is not None


def test_toast_container_is_available():
    dom = _load_dom()

    container = dom.find("div", id="toastContainer")
    assert container is not None
    assert "toast-container" in container.get("class", [])
    # should be the first element in body to avoid overlaying content
    assert container.parent.name == "body"


def test_bollinger_history_card_structure():
    dom = _load_dom()

    card = dom.find("h2", string=lambda s: s and "Saved Coin History" in s)
    assert card is not None

    table = dom.find("table", id="bollHistoryTable")
    assert table is not None

    headers = [th.get_text(strip=True) for th in table.select("thead th")]
    assert headers == ["Time", "Price", "MA", "Upper", "Lower"]

    tbody = table.find("tbody", id="bollHistoryBody")
    assert tbody is not None

    status = dom.find("p", id="bollHistoryStatus")
    assert status is not None
