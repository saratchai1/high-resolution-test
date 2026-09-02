from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path

import yaml
from shapely.geometry import shape


@dataclass(frozen=True)
class MonthWindow:
    month: str
    start: datetime
    end: datetime


def load_config(path: str | Path) -> dict:
    path = Path(path)
    cfg = yaml.safe_load(path.read_text(encoding="utf-8"))
    cfg["_config_path"] = str(path)
    return cfg


def load_aoi(path: str | Path):
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if data.get("type") == "FeatureCollection":
        feat = data["features"][0]
    elif data.get("type") == "Feature":
        feat = data
    else:
        feat = {"geometry": data, "properties": {}}
    geom = shape(feat["geometry"])
    if geom.is_empty or not geom.is_valid:
        raise ValueError(f"Invalid AOI geometry: {path}")
    return feat, geom


def parse_month(value: str) -> date:
    y, m = (int(x) for x in value.split("-"))
    return date(y, m, 1)


def add_months(d: date, months: int) -> date:
    idx = d.year * 12 + (d.month - 1) + months
    return date(idx // 12, idx % 12 + 1, 1)


def month_windows(start_month: str, count: int) -> list[MonthWindow]:
    start = parse_month(start_month)
    out: list[MonthWindow] = []
    for i in range(count):
        a = add_months(start, i)
        b = add_months(a, 1)
        out.append(
            MonthWindow(
                month=a.strftime("%Y-%m"),
                start=datetime(a.year, a.month, 1, tzinfo=timezone.utc),
                end=datetime(b.year, b.month, 1, tzinfo=timezone.utc),
            )
        )
    return out


def full_period_months(start_month: str, end_month_exclusive: str) -> list[str]:
    a = parse_month(start_month)
    b = parse_month(end_month_exclusive)
    out = []
    cur = a
    while cur < b:
        out.append(cur.strftime("%Y-%m"))
        cur = add_months(cur, 1)
    return out


def iso_range(start: datetime, end: datetime) -> str:
    return f"{start.isoformat().replace('+00:00','Z')}/{end.isoformat().replace('+00:00','Z')}"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")
