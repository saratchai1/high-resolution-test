# 13-STC · 36-Month Sentinel-2 Change Viewer

## Live web app

**Production:** https://13-stc-36-month-change-viewer.vercel.app

Interactive viewer for **36 complete months (2023-09 → 2026-08)** with:

- timeline slider + autoplay
- native Sentinel-2 10 m ↔ LDSR-S2 ~2.5 m swipe comparison
- zoom / pan / reset
- authoritative **13-STC KMZ boundary** overlay (`ยืนยันกรม`, reported 263.07 rai)
- monthly clear coverage, scene count, and cloud-fallback status

This repository builds one cloud-masked Sentinel-2 L2A RGB/NIR image per full month for **13-STC, Rayong** and runs **LDSR-S2 / OpenSR 4× super-resolution** through `geoai-py[sr]`.

## Scope

- AOI: 13-STC authoritative boundary (`ยืนยันกรม`, reported 263.07 rai) extracted from the supplied KMZ.
- Reference point: `12.707884, 101.693164`.
- Period: **2023-09 through 2026-08** (36 complete months).
- Source: Element 84 Earth Search `sentinel-2-l2a`.
- Native bands: B04 Red, B03 Green, B02 Blue, B08 NIR at 10 m.
- Cloud removal: SCL clear-pixel mask + masked median of all usable scenes in each month; an adjacent-date fallback window is used only when a month cannot reach the configured valid-pixel threshold.
- SR: LDSR-S2/OpenSR, 4×, 25 diffusion sampling steps, output on a 2.5 m grid.

The 2.5 m grid is **model-reconstructed detail**, not native Sentinel-2 sensor resolution.

The super-resolution implementation follows `skills/sentinel-2-super-resolution/SKILL.md` from `saratchai1/corrosion` commit `af23885d8605c9c74c3e81bbe176e53df8f1c74c`.

## Reproduce one month

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
python scripts/superres/build_month_v2.py --config config/superres/13-stc.yml --start 2026-08 --months 1
```

## Batch / GitHub Actions

`.github/workflows/monthly-superres.yml` splits 36 months into 12 groups, processes them in parallel, validates every output, and assembles the web dataset. The lightweight WebP products and `summary.json` are committed under `web/public/data/superres25/`; GeoTIFFs remain workflow artifacts.

## Web viewer source

The viewer source is under `web/`:

```bash
python -m http.server 8000 -d web
```

Then open `http://localhost:8000`.
