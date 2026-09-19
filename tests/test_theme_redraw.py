"""A theme toggle redraws charts that are on screen.

Canvas ink is painted with the theme active when the chart was created, so a
toggle left the dashboard's Balance Sheet Trend with light-mode axis text on a
dark panel — 1.06:1 (skytech, 2.16.0 gate). The Analytics page had carried the
same flaw since it shipped. `App.toggleTheme` now announces the change and
both pages redraw.
"""

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
JS = ROOT / "app" / "static" / "js"


def _read(name: str) -> str:
    return (JS / name).read_text(encoding="utf-8")


def test_toggle_announces_and_both_chart_pages_listen():
    assert "new CustomEvent('slowbooks:themechange'" in _read("app.js")
    assert "addEventListener('slowbooks:themechange'" in _read("dashboard.js")
    analytics = _read("analytics.js")
    assert 'addEventListener("slowbooks:themechange"' in analytics
    # the redraw is a no-op when the page is not on screen
    assert 'if (!document.getElementById("chart-ar-aging")) return;' in analytics


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_trend_is_redrawn_in_the_new_themes_ink():
    out = subprocess.run(
        ["node", str(ROOT / "tests" / "js" / "theme_redraw_probe.js")],
        cwd=ROOT,
        capture_output=True,
        text=True,
        timeout=60,
    )
    assert out.returncode == 0, out.stderr
    assert "charts made: 2 | destroyed before redraw: 1" in out.stdout
    assert "after toggle  axis #e6e6f0 assets line #4a7fb5" in out.stdout
    assert "listener registered: true" in out.stdout
