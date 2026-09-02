from __future__ import annotations

import argparse
import gc
import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import rasterio
from pyproj import Transformer
from rasterio.features import geometry_mask
from shapely.geometry import box, mapping
from shapely.ops import transform as shapely_transform

from build_month import (
    _composite,
    _read_target_grid,
    _render_web,
    _stac_search,
    _validate_web,
    _write_native,
)
from common import load_aoi, load_config, month_windows, utc_now_iso


def _fractions(valid_mask: np.ndarray, aoi_mask: np.ndarray) -> tuple[float, float]:
    return float(valid_mask[aoi_mask].mean()), float(valid_mask.mean())


def _merge_unique(existing: list[dict], incoming: list[dict]) -> list[dict]:
    seen = {item.get("id") for item in existing}
    return existing + [item for item in incoming if item.get("id") not in seen]


def process_one(cfg: dict, month_window) -> dict:
    feature, aoi = load_aoi(cfg["geometry"])
    reference_lon = float(cfg["reference_point"]["lon"])
    reference_lat = float(cfg["reference_point"]["lat"])
    processing_center = aoi.representative_point()
    lon = float(processing_center.x)
    lat = float(processing_center.y)

    patch_size = int(cfg["processing"]["patch_size"])
    clear_classes = set(int(x) for x in cfg["cloud_mask"]["scl_clear_classes"])
    min_aoi_valid = float(cfg["cloud_mask"]["min_composite_valid_fraction"])
    min_context_valid = float(cfg["cloud_mask"].get("min_context_valid_fraction", 0.90))
    fallback_windows = [int(x) for x in cfg["source"].get("fallback_windows_days", [20, 35, 50])]

    month_items = _stac_search(cfg, aoi, month_window.start, month_window.end)
    if not month_items:
        raise RuntimeError(f"No Sentinel-2 scenes found for {month_window.month}")

    crs, transform = _read_target_grid(month_items[0], lon, lat, patch_size)
    to_projected = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    aoi_projected = shapely_transform(to_projected.transform, aoi)
    patch_bounds = rasterio.transform.array_bounds(patch_size, patch_size, transform)
    patch_polygon = box(*patch_bounds)
    if not patch_polygon.covers(aoi_projected):
        missing_area = aoi_projected.difference(patch_polygon).area
        raise RuntimeError(
            f"Configured {patch_size}x{patch_size} native patch does not cover the full AOI; "
            f"outside area={missing_area:.1f} m2"
        )

    aoi_mask = geometry_mask(
        [mapping(aoi_projected)],
        out_shape=(patch_size, patch_size),
        transform=transform,
        invert=True,
        all_touched=False,
    )
    if not np.any(aoi_mask):
        raise RuntimeError("AOI does not cover any target-grid pixels")

    used_search_items = list(month_items)
    native, valid_mask, used, clear_fracs = _composite(
        used_search_items, crs, transform, patch_size, clear_classes
    )
    aoi_valid_fraction, context_valid_fraction = _fractions(valid_mask, aoi_mask)
    fallback_days_used = 0

    print(
        json.dumps(
            {
                "month": month_window.month,
                "stage": "monthly",
                "aoi_valid_fraction": aoi_valid_fraction,
                "context_valid_fraction": context_valid_fraction,
                "candidate_items": len(used_search_items),
            },
            indent=2,
        )
    )

    for days in fallback_windows:
        if aoi_valid_fraction >= min_aoi_valid and context_valid_fraction >= min_context_valid:
            break
        ext_items = _stac_search(
            cfg,
            aoi,
            month_window.start - timedelta(days=days),
            month_window.end + timedelta(days=days),
        )
        used_search_items = _merge_unique(used_search_items, ext_items)
        native, valid_mask, used, clear_fracs = _composite(
            used_search_items, crs, transform, patch_size, clear_classes
        )
        aoi_valid_fraction, context_valid_fraction = _fractions(valid_mask, aoi_mask)
        fallback_days_used = days
        print(
            json.dumps(
                {
                    "month": month_window.month,
                    "stage": f"fallback_plusminus_{days}d",
                    "aoi_valid_fraction": aoi_valid_fraction,
                    "context_valid_fraction": context_valid_fraction,
                    "candidate_items": len(used_search_items),
                },
                indent=2,
            )
        )

    if aoi_valid_fraction < min_aoi_valid:
        raise RuntimeError(
            f"AOI composite valid fraction {aoi_valid_fraction:.3f} below required "
            f"{min_aoi_valid:.3f} for {month_window.month}"
        )
    if context_valid_fraction < min_context_valid:
        raise RuntimeError(
            f"Context composite valid fraction {context_valid_fraction:.3f} below required "
            f"{min_context_valid:.3f} for {month_window.month}; AOI={aoi_valid_fraction:.3f}"
        )

    month_dir = Path(cfg["outputs"]["root"]) / month_window.month
    native_tif = month_dir / "native_rgbnir.tif"
    sr_tif = month_dir / "superres_rgbnir.tif"
    metadata_path = month_dir / "metadata.json"
    _write_native(native_tif, native, crs, transform)

    nonzero_fraction = float(np.count_nonzero(native[:3]) / native[:3].size)
    if nonzero_fraction + 1e-6 < min_context_valid:
        raise RuntimeError(
            f"native_rgb_nonzero_fraction {nonzero_fraction:.3f} below context threshold "
            f"{min_context_valid:.3f}"
        )

    import geoai

    geoai.super_resolution(
        input_lr_path=str(native_tif),
        output_sr_path=str(sr_tif),
        rgb_nir_bands=[1, 2, 3, 4],
        sampling_steps=int(cfg["processing"]["sampling_steps"]),
        scale=int(cfg["processing"]["scale"]),
        compute_uncertainty=bool(cfg["processing"]["compute_uncertainty"]),
        scale_factor=float(cfg["processing"]["scale_factor"]),
        patch_size=patch_size,
        overlap=int(cfg["processing"]["overlap"]),
    )

    with rasterio.open(sr_tif) as src:
        expected = patch_size * int(cfg["processing"]["scale"])
        if src.width != expected or src.height != expected:
            raise RuntimeError(f"Unexpected SR size: {src.width}x{src.height}; expected {expected}x{expected}")
        xres = abs(float(src.transform.a))
        yres = abs(float(src.transform.e))
        if not (2.3 <= xres <= 2.7 and 2.3 <= yres <= 2.7):
            raise RuntimeError(f"Unexpected SR grid: {xres:.3f} x {yres:.3f} m")

    web_dir = month_dir / "web"
    native_webp = web_dir / f"{month_window.month}-native.webp"
    sr_webp = web_dir / f"{month_window.month}-sr.webp"
    stretch = _render_web(native, sr_tif, native_webp, sr_webp, cfg["render"])
    _validate_web(native_webp, int(cfg["render"]["web_size"]))
    _validate_web(sr_webp, int(cfg["render"]["web_size"]))

    item_meta = []
    for item, clear_fraction in zip(used, clear_fracs):
        props = item.get("properties", {})
        item_meta.append(
            {
                "id": item.get("id"),
                "datetime": props.get("datetime"),
                "eo_cloud_cover": props.get("eo:cloud_cover"),
                "mgrs_tile": props.get("mgrs:tile") or props.get("s2:mgrs_tile"),
                "scene_clear_fraction_on_target_grid": round(float(clear_fraction), 6),
            }
        )

    bounds = rasterio.transform.array_bounds(patch_size, patch_size, transform)
    metadata = {
        "plot_id": cfg["plot_id"],
        "month": month_window.month,
        "period_start": month_window.start.isoformat().replace("+00:00", "Z"),
        "period_end_exclusive": month_window.end.isoformat().replace("+00:00", "Z"),
        "generated_at": utc_now_iso(),
        "source": {
            "provider": "Element 84 Earth Search",
            "stac_api": cfg["source"]["stac_api"],
            "collection": cfg["source"]["collection"],
            "items": item_meta,
            "fallback_window_used": fallback_days_used > 0,
            "fallback_days_each_side_used": fallback_days_used,
            "monthly_candidate_count": len(month_items),
            "final_candidate_count": len(used_search_items),
        },
        "aoi": {
            "geometry_file": cfg["geometry"],
            "source_record": feature.get("properties", {}).get("source_record"),
            "reference_point": {"lon": reference_lon, "lat": reference_lat},
            "processing_center": {"lon": lon, "lat": lat},
        },
        "processing": {
            "cloud_mask": "Sentinel-2 SCL clear classes + multi-scene masked median",
            "scl_clear_classes": sorted(clear_classes),
            "composite_valid_fraction": round(aoi_valid_fraction, 6),
            "aoi_valid_fraction": round(aoi_valid_fraction, 6),
            "context_valid_fraction": round(context_valid_fraction, 6),
            "min_aoi_valid_fraction": min_aoi_valid,
            "min_context_valid_fraction": min_context_valid,
            "native_rgb_nonzero_fraction": round(nonzero_fraction, 6),
            "band_order": ["B04 Red", "B03 Green", "B02 Blue", "B08 NIR"],
            "model": "LDSR-S2 / OpenSR via geoai-py",
            "model_scale": int(cfg["processing"]["scale"]),
            "sampling_steps": int(cfg["processing"]["sampling_steps"]),
            "scale_factor": float(cfg["processing"]["scale_factor"]),
            "input_patch_pixels": patch_size,
            "output_pixels": patch_size * int(cfg["processing"]["scale"]),
            "output_grid_m": float(cfg["processing"]["output_grid_m"]),
            "display_stretch_from_native": stretch,
        },
        "spatial": {
            "crs": str(crs),
            "native_transform": list(transform)[:6],
            "native_bounds_projected": list(bounds),
        },
        "files": {
            "native_tif": native_tif.name,
            "superres_tif": sr_tif.name,
            "native_webp": f"web/{native_webp.name}",
            "superres_webp": f"web/{sr_webp.name}",
        },
    }
    metadata_path.write_text(json.dumps(metadata, ensure_ascii=False, indent=2), encoding="utf-8")
    print(
        json.dumps(
            {
                "month": month_window.month,
                "status": "done",
                "aoi_valid_fraction": aoi_valid_fraction,
                "context_valid_fraction": context_valid_fraction,
                "fallback_days_each_side_used": fallback_days_used,
                "items": len(item_meta),
            },
            indent=2,
        )
    )
    gc.collect()
    return metadata


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--config", required=True)
    parser.add_argument("--start", required=True, help="YYYY-MM")
    parser.add_argument("--months", type=int, default=1)
    args = parser.parse_args()
    cfg = load_config(args.config)
    for mw in month_windows(args.start, args.months):
        process_one(cfg, mw)


if __name__ == "__main__":
    main()
