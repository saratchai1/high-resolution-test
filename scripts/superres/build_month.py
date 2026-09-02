from __future__ import annotations

import argparse
import gc
import json
import warnings
from datetime import timedelta
from pathlib import Path
from typing import Any

import numpy as np
import requests
import rasterio
from PIL import Image, ImageStat
from pyproj import Transformer
from rasterio.enums import Resampling
from rasterio.features import geometry_mask
from rasterio.transform import Affine
from rasterio.vrt import WarpedVRT
from rasterio.windows import Window
from shapely.geometry import box, shape, mapping
from shapely.ops import transform as shapely_transform

from common import iso_range, load_aoi, load_config, month_windows, utc_now_iso

ASSET_KEYS = ("red", "green", "blue", "nir")


def _stac_search(cfg: dict, aoi_geom, start, end) -> list[dict[str, Any]]:
    src = cfg["source"]
    payload = {
        "collections": [src["collection"]],
        "intersects": mapping(aoi_geom),
        "datetime": iso_range(start, end),
        "limit": 100,
        "query": {"eo:cloud_cover": {"lte": src["max_scene_cloud_percent"]}},
    }
    r = requests.post(f"{src['stac_api'].rstrip('/')}/search", json=payload, timeout=60)
    r.raise_for_status()
    features = r.json().get("features", [])
    center = aoi_geom.representative_point()
    ranked = []
    for item in features:
        assets = item.get("assets", {})
        if not all(k in assets for k in (*ASSET_KEYS, "scl")):
            continue
        try:
            footprint = shape(item["geometry"])
            if not footprint.intersects(aoi_geom) or not footprint.covers(center):
                continue
            cov = footprint.intersection(aoi_geom).area / max(aoi_geom.area, 1e-18)
        except Exception:
            continue
        cloud = float(item.get("properties", {}).get("eo:cloud_cover") or 100.0)
        dt = item.get("properties", {}).get("datetime") or ""
        ranked.append((-(cov), cloud, dt, item))
    ranked.sort(key=lambda x: (x[0], x[1], x[2]))
    return [x[-1] for x in ranked[: int(src["max_scenes_per_month"])]]


def _read_target_grid(reference_item: dict, lon: float, lat: float, patch_size: int):
    href = reference_item["assets"]["red"]["href"]
    with rasterio.Env(
        AWS_NO_SIGN_REQUEST="YES",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
    ):
        with rasterio.open(href) as src:
            transformer = Transformer.from_crs("EPSG:4326", src.crs, always_xy=True)
            x, y = transformer.transform(lon, lat)
            row, col = src.index(x, y)
            half = patch_size // 2
            window = Window(col - half, row - half, patch_size, patch_size)
            return src.crs, src.window_transform(window)


def _read_asset_to_grid(href: str, crs, transform: Affine, size: int, resampling: Resampling) -> np.ndarray:
    with rasterio.Env(
        AWS_NO_SIGN_REQUEST="YES",
        GDAL_DISABLE_READDIR_ON_OPEN="EMPTY_DIR",
        CPL_VSIL_CURL_ALLOWED_EXTENSIONS=".tif,.TIF",
    ):
        with rasterio.open(href) as src:
            with WarpedVRT(
                src,
                crs=crs,
                transform=transform,
                width=size,
                height=size,
                resampling=resampling,
                nodata=0,
            ) as vrt:
                return vrt.read(1, out_dtype="float32")


def _read_item(item: dict, crs, transform: Affine, size: int, clear_classes: set[int]):
    bands = []
    for key in ASSET_KEYS:
        raw = _read_asset_to_grid(item["assets"][key]["href"], crs, transform, size, Resampling.bilinear)
        raw = np.clip(np.rint(raw), 0, 10000).astype(np.float32)
        bands.append(raw)
    data = np.stack(bands)
    scl = _read_asset_to_grid(item["assets"]["scl"]["href"], crs, transform, size, Resampling.nearest)
    scl_i = np.rint(scl).astype(np.int16)
    clear = np.isin(scl_i, list(clear_classes)) & np.all(data > 0, axis=0)
    data[:, ~clear] = np.nan
    return data, clear


def _composite(items: list[dict], crs, transform: Affine, size: int, clear_classes: set[int]):
    cubes = []
    clear_fracs = []
    used = []
    for item in items:
        try:
            cube, clear = _read_item(item, crs, transform, size, clear_classes)
        except Exception as exc:
            print(f"WARN read failed {item.get('id')}: {exc}")
            continue
        scene_clear = float(clear.mean())
        if scene_clear <= 0.01:
            continue
        cubes.append(cube)
        clear_fracs.append(scene_clear)
        used.append(item)
    if not cubes:
        raise RuntimeError("No readable Sentinel-2 candidates with valid RGB/NIR pixels")
    stack = np.stack(cubes, axis=0)
    with warnings.catch_warnings():
        warnings.filterwarnings("ignore", message="All-NaN slice encountered", category=RuntimeWarning)
        comp = np.nanmedian(stack, axis=0)
    valid = np.all(np.isfinite(comp), axis=0)
    comp = np.nan_to_num(comp, nan=0.0, posinf=0.0, neginf=0.0)
    comp = np.clip(np.rint(comp), 0, 10000).astype(np.uint16)
    return comp, valid, used, clear_fracs


def _write_native(path: Path, arr: np.ndarray, crs, transform: Affine):
    path.parent.mkdir(parents=True, exist_ok=True)
    with rasterio.open(
        path,
        "w",
        driver="GTiff",
        width=arr.shape[2],
        height=arr.shape[1],
        count=4,
        dtype="uint16",
        crs=crs,
        transform=transform,
        tiled=True,
        compress="deflate",
    ) as dst:
        dst.write(arr)
        dst.set_band_description(1, "Red")
        dst.set_band_description(2, "Green")
        dst.set_band_description(3, "Blue")
        dst.set_band_description(4, "NIR")


def _stretch_limits(native: np.ndarray, low: float, high: float):
    limits = []
    for b in native[:3].astype(np.float32):
        valid = b[np.isfinite(b) & (b > 0)]
        if valid.size < 100:
            raise RuntimeError("Too few valid RGB pixels for display stretch")
        lo, hi = np.percentile(valid, [low, high])
        if not np.isfinite(lo) or not np.isfinite(hi) or hi <= lo:
            raise RuntimeError("Invalid RGB stretch")
        limits.append((float(lo), float(hi)))
    return limits


def _rgb8(arr: np.ndarray, limits, gamma: float) -> np.ndarray:
    rgb = arr[:3].astype(np.float32)
    if np.nanmax(rgb) <= 2.0:
        rgb *= 10000.0
    out = np.zeros((rgb.shape[1], rgb.shape[2], 3), dtype=np.uint8)
    for i, (lo, hi) in enumerate(limits):
        x = np.clip((rgb[i] - lo) / (hi - lo), 0, 1)
        if gamma != 1.0:
            x = np.power(x, 1.0 / gamma)
        out[:, :, i] = np.rint(x * 255).astype(np.uint8)
    return out


def _render_web(native: np.ndarray, sr_tif: Path, native_webp: Path, sr_webp: Path, render_cfg: dict):
    limits = _stretch_limits(native, render_cfg["percentile_low"], render_cfg["percentile_high"])
    with rasterio.open(sr_tif) as src:
        sr = src.read().astype(np.float32)
    native_img = Image.fromarray(_rgb8(native, limits, float(render_cfg["gamma"])), "RGB")
    sr_img = Image.fromarray(_rgb8(sr, limits, float(render_cfg["gamma"])), "RGB")
    target = int(render_cfg["web_size"])
    native_img = native_img.resize((target, target), Image.Resampling.NEAREST)
    if sr_img.size != (target, target):
        sr_img = sr_img.resize((target, target), Image.Resampling.LANCZOS)
    native_webp.parent.mkdir(parents=True, exist_ok=True)
    sr_webp.parent.mkdir(parents=True, exist_ok=True)
    native_img.save(native_webp, "WEBP", quality=88, method=6)
    sr_img.save(sr_webp, "WEBP", quality=88, method=6)
    return limits


def _validate_web(path: Path, expected_size: int):
    if not path.exists() or path.stat().st_size < 4_000:
        raise RuntimeError(f"Web image missing or suspiciously small: {path}")
    image = Image.open(path).convert("RGB")
    if image.size != (expected_size, expected_size):
        raise RuntimeError(f"Unexpected web image size {image.size}: {path}")
    extrema = ImageStat.Stat(image).extrema
    if not any(high - low > 10 for low, high in extrema):
        raise RuntimeError(f"Flat/black-looking image rejected: {path}")


def process_one(cfg: dict, month_window) -> dict:
    feature, aoi = load_aoi(cfg["geometry"])
    reference_lon = float(cfg["reference_point"]["lon"])
    reference_lat = float(cfg["reference_point"]["lat"])
    processing_center = aoi.representative_point()
    lon = float(processing_center.x)
    lat = float(processing_center.y)
    patch_size = int(cfg["processing"]["patch_size"])
    clear_classes = set(int(x) for x in cfg["cloud_mask"]["scl_clear_classes"])
    min_valid = float(cfg["cloud_mask"]["min_composite_valid_fraction"])

    items = _stac_search(cfg, aoi, month_window.start, month_window.end)
    if not items:
        raise RuntimeError(f"No Sentinel-2 scenes found for {month_window.month}")
    crs, transform = _read_target_grid(items[0], lon, lat, patch_size)
    to_projected = Transformer.from_crs("EPSG:4326", crs, always_xy=True)
    aoi_projected = shapely_transform(to_projected.transform, aoi)
    patch_bounds = rasterio.transform.array_bounds(patch_size, patch_size, transform)
    patch_polygon = box(patch_bounds[0], patch_bounds[1], patch_bounds[2], patch_bounds[3])
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

    native, valid_mask, used, clear_fracs = _composite(items, crs, transform, patch_size, clear_classes)
    context_valid_fraction = float(valid_mask.mean())
    aoi_valid_fraction = float(valid_mask[aoi_mask].mean())
    fallback_used = False
    if aoi_valid_fraction < min_valid:
        days = int(cfg["source"].get("fallback_days_each_side", 0))
        if days > 0:
            ext_items = _stac_search(
                cfg,
                aoi,
                month_window.start - timedelta(days=days),
                month_window.end + timedelta(days=days),
            )
            seen = {x.get("id") for x in items}
            merged = items + [x for x in ext_items if x.get("id") not in seen]
            native, valid_mask, used, clear_fracs = _composite(merged, crs, transform, patch_size, clear_classes)
            context_valid_fraction = float(valid_mask.mean())
            aoi_valid_fraction = float(valid_mask[aoi_mask].mean())
            fallback_used = True
    if aoi_valid_fraction < min_valid:
        raise RuntimeError(
            f"AOI composite valid fraction {aoi_valid_fraction:.3f} below required {min_valid:.3f} "
            f"for {month_window.month} (context={context_valid_fraction:.3f})"
        )

    month_dir = Path(cfg["outputs"]["root"]) / month_window.month
    native_tif = month_dir / "native_rgbnir.tif"
    sr_tif = month_dir / "superres_rgbnir.tif"
    metadata_path = month_dir / "metadata.json"
    _write_native(native_tif, native, crs, transform)

    nonzero_fraction = float(np.count_nonzero(native[:3]) / native[:3].size)
    if nonzero_fraction < 0.90:
        raise RuntimeError(f"native_rgb_nonzero_fraction too low: {nonzero_fraction:.3f}")

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
        if src.width != patch_size * int(cfg["processing"]["scale"]) or src.height != patch_size * int(cfg["processing"]["scale"]):
            raise RuntimeError(f"Unexpected SR size: {src.width}x{src.height}")
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
            "fallback_window_used": fallback_used,
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
    print(json.dumps({
        "month": month_window.month,
        "aoi_valid_fraction": aoi_valid_fraction,
        "context_valid_fraction": context_valid_fraction,
        "items": len(item_meta),
    }, indent=2))
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
