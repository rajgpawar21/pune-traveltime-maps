"""python -m traveltime.build --city pune [--router builtin|osrm]"""
from __future__ import annotations

import argparse
import csv
import json
from pathlib import Path

import numpy as np
import shapely
import yaml

from . import embed, metrics, proximity, router
from .geo import make_grid, projection_for
from .osm_scan import cached_scan

ROOT = Path(__file__).resolve().parent.parent
ROAD_GROUPS = {
    "major": ("motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link"),
    "secondary": ("secondary", "secondary_link"),
    "tertiary": ("tertiary", "tertiary_link"),
    "minor": ("unclassified", "residential", "living_street"),
}


def r3(a):
    return np.round(np.asarray(a, float), 3).tolist()


def vector_layers(scan, proj, extent, ):
    xmin, ymin, xmax, ymax = extent

    def to_km(ll):
        x, y = proj.fwd(ll[:, 0], ll[:, 1])
        return np.column_stack((x, y))

    def in_ext(c):
        return ((c[:, 0] >= xmin) & (c[:, 0] <= xmax) & (c[:, 1] >= ymin) & (c[:, 1] <= ymax)).any()

    def lines(items, tol):
        geoms = []
        for c in items:
            k = to_km(c)
            if in_ext(k):
                geoms.append(shapely.linestrings(k))
        if not geoms:
            return []
        simp = shapely.simplify(np.array(geoms), tol)
        return [r3(shapely.get_coordinates(g)).__class__(np.round(shapely.get_coordinates(g), 3).ravel().tolist())
                for g in simp if not shapely.is_empty(g)]

    roads = {}
    for group, classes in ROAD_GROUPS.items():
        items = [ll for cls, _, _, ll in scan.roads if cls in classes]
        roads[group] = lines(items, 0.003 if group != "minor" else 0.008)
    rail = {"rail": lines([c for k, c in scan.rail if k == "rail"], 0.01),
            "metro": lines([c for k, c in scan.rail if k in ("subway", "light_rail", "monorail")], 0.01)}

    def polys(rings, min_km2):
        out = []
        for ll in rings:
            k = to_km(ll)
            if not in_ext(k):
                continue
            p = shapely.polygons(k)
            if not shapely.is_valid(p):
                p = shapely.make_valid(p)
            if shapely.area(p) < min_km2:
                continue
            p = shapely.simplify(p, 0.01)
            if shapely.is_empty(p):
                continue
            for g in shapely.get_parts(p):
                if g.geom_type == "Polygon" and not g.is_empty:
                    out.append(np.round(shapely.get_coordinates(g.exterior), 3).ravel().tolist())
        return out

    places = []
    for name, rank, lon, lat in scan.places:
        x, y = proj.fwd(lon, lat)
        if xmin <= x <= xmax and ymin <= y <= ymax:
            places.append({"n": name, "r": rank, "x": round(float(x), 3), "y": round(float(y), 3)})
    stations = []
    for name, metro, lon, lat in scan.stations:
        x, y = proj.fwd(lon, lat)
        if xmin <= x <= xmax and ymin <= y <= ymax:
            stations.append({"n": name, "m": int(metro), "x": round(float(x), 3), "y": round(float(y), 3)})
    return {
        "roads": roads, "rail": rail,
        "waterways": lines(scan.waterways, 0.01),
        "water": polys(scan.water, 0.002), "green": polys(scan.green, 0.01),
        "places": places, "stations": stations,
    }


def build_city(key, cfg, defaults, router_kind, out_web: Path, out_csv: Path):
    cfg = {**defaults, **cfg}
    proj = projection_for(cfg["bbox"])
    grid_xy, grid_ll, nx_, ny_, cell = make_grid(cfg["bbox"], cfg["grid_km"], proj)
    n = len(grid_xy)
    print(f"[{key}] grid {nx_}x{ny_} = {n} cells of {cell[0]:.2f}x{cell[1]:.2f} km")

    snaps = []
    for s in cfg["snapshots"]:
        scan = None
        if s.get("pbf"):
            scan = cached_scan(ROOT / s["pbf"], ROOT / ".cache" / key, cfg.get("scan_margin"))
        if s.get("matrix"):
            minutes, snap_km = np.load(ROOT / s["matrix"]), np.zeros(n)
        elif router_kind == "osrm":
            minutes, snap_km = router.osrm_matrix(grid_ll), np.zeros(n)
        else:
            if scan is None:
                raise ValueError(f"snapshot {s['id']} needs a pbf or matrix")
            minutes, snap_km = router.builtin_matrix(scan, proj, grid_xy, s.get("speed_scale"),
                                                     cfg["off_network_km"])
        print(f"[{key}] {s['id']}: routed, median trip {np.nanmedian(minutes):.1f} min")
        snaps.append({"cfg": s, "scan": scan, "minutes": minutes, "snap_km": snap_km})

    valid = np.all([sn["snap_km"] <= cfg["off_network_km"] for sn in snaps], axis=0)
    vi = np.flatnonzero(valid)
    print(f"[{key}] {valid.sum()}/{n} cells on the road network")

    geo_d = embed.pairwise(grid_xy)
    sym = [embed.symmetrise(sn["minutes"]) for sn in snaps]
    # reference speed fixed from the baseline snapshot: median geo-km per minute-hour
    pair = (geo_d > 2) & (geo_d < 10) & valid[:, None] & valid[None, :]
    v_ref = float(np.median(geo_d[pair] / (sym[0][pair] / 60.0)))
    print(f"[{key}] reference speed {v_ref:.1f} km/h (fixed across snapshots)")

    # display extent and lattice
    xmin, ymin = grid_xy.min(0) - np.array(cell) / 2
    xmax, ymax = grid_xy.max(0) + np.array(cell) / 2
    core = (xmin, ymin, xmax, ymax)
    mx, my = 0.14 * (xmax - xmin), 0.14 * (ymax - ymin)
    display = (xmin - mx, ymin - my, xmax + mx, ymax + my)
    pad = (xmin - 0.06 * (xmax - xmin), ymin - 0.06 * (ymax - ymin),
           xmax + 0.06 * (xmax - xmin), ymax + 0.06 * (ymax - ymin))
    lattice = embed.Lattice(display, 0.25)

    fields, meta_snaps = [], []
    G = grid_xy[vi]
    X_prev = None
    ref_pct = {}
    cells = {"act": [], "acc": [], "M": [], "J": [], "fric": [], "cluster": []}
    for si, sn in enumerate(snaps):
        D_km = sym[si] * v_ref / 60.0
        sub = D_km[np.ix_(vi, vi)]
        X0 = G.copy() if X_prev is None else X_prev
        X, stress = embed.weighted_smacof(sub, X0, alpha=cfg["smacof_alpha"])
        X = embed.rigid_align(X, G) if X_prev is None else embed.rigid_align(X, X_prev)
        X_prev = X if si == 0 else X_prev  # every later snapshot aligns to the baseline frame
        field = embed.fit_field(G, X, lattice, core, pad)
        fid = embed.fidelity(embed.apply_field(lattice, field["displacement"], G), sub, G)
        bands = ", ".join(f"{b['band']}km map {b['err_map']:.0%} vs geo {b['err_geo']:.0%}" for b in fid["bands_km"])
        print(f"[{key}] {sn['cfg']['id']}: smoothing {field['smoothing']}, alpha {field['alpha']:.2f}, "
              f"min Jacobian {field['min_j']:.2f}, rank-corr map {fid['spearman_map']:.3f} "
              f"vs geo {fid['spearman_geo']:.3f}; median error: {bands}")
        fields.append(field)

        # per-cell metrics
        scan = sn["scan"]
        if scan is not None:
            raw = metrics.raw_activity(metrics.activity_counts(scan, proj, grid_xy, cell))
        else:
            raw = np.zeros(n)
        act = np.log1p(raw)
        acc = metrics.gravity_accessibility(sym[si], raw, cfg["half_life_min"], valid)
        if si == 0:
            ref_pct = {"act": act[valid].copy(), "acc": acc[valid].copy()}
        M = np.sqrt(metrics.percentile_vs(ref_pct["act"], act) * metrics.percentile_vs(ref_pct["acc"], acc))
        cl = metrics.clusters(M, valid, (ny_, nx_), cfg["score_threshold"], cfg["min_cluster_cells"])
        fric = metrics.friction(D_km, grid_xy)
        jx = np.clip(np.round((grid_xy[:, 0] - lattice.x0) / lattice.step).astype(int), 0, lattice.nx - 1)
        jy = np.clip(np.round((grid_xy[:, 1] - lattice.y0) / lattice.step).astype(int), 0, lattice.ny - 1)
        Jc = field["jacobian"][jy, jx]
        for k, v in (("act", act), ("acc", acc), ("M", M), ("J", Jc), ("fric", fric), ("cluster", cl)):
            cells[k].append(v)
        meta_snaps.append({"id": sn["cfg"]["id"], "label": sn["cfg"].get("label", sn["cfg"]["id"]),
                           "scenario": bool(sn["cfg"].get("speed_scale")),
                           "median_trip_min": float(np.nanmedian(sn["minutes"])),
                           "smoothing": field["smoothing"], "alpha": field["alpha"],
                           "min_jacobian": field["min_j"], "fidelity": fid})

    # ---- export for the viewer
    city_dir = out_web / key
    city_dir.mkdir(parents=True, exist_ok=True)
    base_scan = next(sn["scan"] for sn in snaps if sn["scan"] is not None)
    (city_dir / "vector.json").write_text(
        json.dumps(vector_layers(base_scan, proj, display), separators=(",", ":")))
    (city_dir / "fields.json").write_text(json.dumps({
        "nx": lattice.nx, "ny": lattice.ny, "x0": lattice.x0, "y0": lattice.y0, "step": lattice.step,
        "dx": [r3(f["displacement"][:, 0]) for f in fields],
        "dy": [r3(f["displacement"][:, 1]) for f in fields],
    }, separators=(",", ":")))
    cell_json = {"x": r3(grid_xy[:, 0]), "y": r3(grid_xy[:, 1]), "valid": valid.astype(int).tolist(),
                 "w": round(cell[0], 3), "h": round(cell[1], 3)}
    for k, arrs in cells.items():
        cell_json[k] = [np.round(np.nan_to_num(a, nan=0.0), 3).tolist() for a in arrs]
    (city_dir / "cells.json").write_text(json.dumps(cell_json, separators=(",", ":")))
    (city_dir / "meta.json").write_text(json.dumps({
        "key": key, "name": cfg["name"], "tier": cfg.get("tier"), "display": display, "core": core,
        "ref_speed_kmh": v_ref, "grid_km": cfg["grid_km"], "snapshots": meta_snaps,
        "half_life_min": cfg["half_life_min"], "score_threshold": cfg["score_threshold"],
        "lonlat0": [proj.lon0, proj.lat0], "has_proximity": bool(cfg.get("proximity")),
    }, indent=1))

    if cfg.get("proximity"):
        proximity.build(key, cfg, proj, grid_xy, cell, nx_, ny_, valid, v_ref, lattice, core, pad, base_scan,
                        city_dir, out_csv, ROOT)

    # ---- ranked micro-market table
    out_csv.mkdir(parents=True, exist_ok=True)
    last, first = len(snaps) - 1, 0
    cl = cells["cluster"][last]
    places = base_scan.places
    prj = [(nm, rk, *proj.fwd(lo, la)) for nm, rk, lo, la in places]
    rows = []
    for cid in sorted(set(cl[cl > 0].tolist())):
        m = cl == cid
        cx, cy = grid_xy[m].mean(0)
        near = sorted(((np.hypot(px - cx, py - cy), rk, nm) for nm, rk, px, py in prj
                       if rk in (1, 2) and np.hypot(px - cx, py - cy) < 3.0))[:3]
        lon, lat = proj.inv(cx, cy)
        rows.append({
            "cluster": cid, "areas": "; ".join(nm for _, _, nm in near) or "(unnamed)",
            "cells": int(m.sum()), "area_km2": round(m.sum() * cell[0] * cell[1], 1),
            "lat": round(float(lat), 5), "lon": round(float(lon), 5),
            "micro_market_score": round(float(cells["M"][last][m].mean()), 3),
            "delta_score": round(float((cells["M"][last][m] - cells["M"][first][m]).mean()), 3) if last else "",
            "delta_accessibility": round(float((cells["acc"][last][m] - cells["acc"][first][m]).mean()), 3) if last else "",
            "delta_activity": round(float((cells["act"][last][m] - cells["act"][first][m]).mean()), 3) if last else "",
            "mean_inflation_J": round(float(cells["J"][last][m].mean()), 2),
        })
    key_fn = (lambda r: -r["delta_score"]) if last else (lambda r: -r["micro_market_score"])
    rows.sort(key=key_fn)
    with open(out_csv / f"{key}_micromarkets.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["cluster"])
        w.writeheader()
        w.writerows(rows)
    print(f"[{key}] {len(rows)} micro-market clusters -> {out_csv / (key + '_micromarkets.csv')}")
    return {"key": key, "name": cfg["name"], "tier": cfg.get("tier")}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--city", action="append", help="city key (repeatable); default all")
    ap.add_argument("--config", default=str(ROOT / "config" / "cities.yaml"))
    ap.add_argument("--router", choices=["builtin", "osrm"], default="builtin")
    ap.add_argument("--out", default=str(ROOT / "web" / "data"))
    args = ap.parse_args()
    conf = yaml.safe_load(Path(args.config).read_text())
    keys = args.city or list(conf["cities"])
    out_web = Path(args.out)
    built = [build_city(k, conf["cities"][k], conf.get("defaults", {}), args.router, out_web,
                        ROOT / "outputs") for k in keys]
    index_path = out_web / "index.json"
    existing = json.loads(index_path.read_text()) if index_path.exists() else []
    merged = {c["key"]: c for c in existing}
    merged.update({c["key"]: c for c in built})
    index_path.write_text(json.dumps(list(merged.values()), indent=1))


if __name__ == "__main__":
    main()
