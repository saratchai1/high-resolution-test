import sys
from pathlib import Path
sys.path.insert(0, str(Path(__file__).parents[1] / "scripts" / "superres"))
from common import full_period_months, month_windows


def test_three_year_period_is_36_months():
    months = full_period_months("2023-09", "2026-09")
    assert len(months) == 36
    assert months[0] == "2023-09"
    assert months[-1] == "2026-08"


def test_month_window_end_is_exclusive():
    w = month_windows("2024-02", 1)[0]
    assert w.start.strftime("%Y-%m-%d") == "2024-02-01"
    assert w.end.strftime("%Y-%m-%d") == "2024-03-01"
