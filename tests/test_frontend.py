from pathlib import Path

import pytest

pytest.importorskip("bs4")
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


def test_mean_and_bollinger_forms_exist():
    dom = _load_dom()

    assert dom.find("form", id="configForm") is not None
    assert dom.find("form", id="manualTradeForm") is not None
    assert dom.find("form", id="bollConfigForm") is not None
