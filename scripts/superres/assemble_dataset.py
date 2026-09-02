from __future__ import annotations

import argparse
import json
import shutil
from pathlib import Path

from common import full_period_months, load_config, utc_now_iso


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--artifact-root", required=True)
    p.add_argument("--config", required=True)
    args = p.parse_args()

    cfg = load_config(args.config)
    artifact_root = Path(args.artifact_root)
    web_dir = Path(cfg["outputs"]["web_data"])
    web_dir.mkdir(parents=True, exist_ok=True)
    expected = full_period_months(cfg["period"]["start_month"], cfg["period"]["end_month_exclusive"])
    entries = []
    missing = []

    for month in expected:
        candidates = list(artifact_root.glob(f"**/{month}/metadata.json"))
        if len(candidates) != 1:
            missing.append({"month": month, "metadata_matches": [str(x) for x in candidates]})
            continue
        meta_path = candidates[0]
        month_dir = meta_path.parent
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        native = month_dir / meta["files"]["native_webp"]
        sr = month_dir / meta["files"]["superres_webp"]
        if not native.exists() or not sr.exists():
            missing.append({"month": month, "reason": "web assets missing"})
            continue
        native_name = f"13-stc-{month}-native.webp"
        sr_name = f"13-stc-{month}-sr.webp"
        shutil.copy2(native, web_dir / native_name)
        shutil.copy2(sr, web_dir / sr_name)
        entries.append(
            {
                "month": month,
                "plot_id": meta["plot_id"],
                "date_label": month,
                "native": f"data/superres25/{native_name}",
                "superres": f"data/superres25/{sr_name}",
                "valid_fraction": meta["processing"]["composite_valid_fraction"],
                "source_items": meta["source"]["items"],
                "fallback_window_used": meta["source"]["fallback_window_used"],
            }
        )

    if missing:
        raise RuntimeError(f"Cannot publish incomplete dataset: {json.dumps(missing, ensure_ascii=False)}")
    if len(entries) != 36:
        raise RuntimeError(f"Expected 36 months, got {len(entries)}")

    summary = {
        "plot_id": cfg["plot_id"],
        "generated_at": utc_now_iso(),
        "period": {"start": expected[0], "end": expected[-1], "months": len(entries)},
        "entries": entries,
    }
    (web_dir / "summary.json").write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8")
    manifest_dir = Path("outputs/manifests")
    manifest_dir.mkdir(parents=True, exist_ok=True)
    (manifest_dir / "13-stc-monthly-summary.json").write_text(
        json.dumps(summary, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    print(f"Published {len(entries)} monthly image pairs to {web_dir}")


if __name__ == "__main__":
    main()
