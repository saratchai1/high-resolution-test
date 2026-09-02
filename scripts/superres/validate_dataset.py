from __future__ import annotations

import argparse
import json
from pathlib import Path
from PIL import Image, ImageStat


def validate_image(path: Path, expected=(512, 512)):
    if not path.exists() or path.stat().st_size < 4_000:
        raise AssertionError(f"missing/small image: {path}")
    im = Image.open(path).convert("RGB")
    assert im.size == expected, (path, im.size)
    extrema = ImageStat.Stat(im).extrema
    assert any(hi - lo > 10 for lo, hi in extrema), (path, extrema)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--summary", default="web/public/data/superres25/summary.json")
    args = p.parse_args()
    summary_path = Path(args.summary)
    summary = json.loads(summary_path.read_text(encoding="utf-8"))
    assert summary["period"]["months"] == 36
    assert len(summary["entries"]) == 36
    months = [e["month"] for e in summary["entries"]]
    assert months == sorted(months)
    root = Path("web/public")
    for e in summary["entries"]:
        assert e["valid_fraction"] >= 0.95
        validate_image(root / e["native"])
        validate_image(root / e["superres"])
    print("dataset validation passed: 36 months")


if __name__ == "__main__":
    main()
