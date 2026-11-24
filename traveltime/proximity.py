"""Proximity lens: Work / Lifestyle / Metro pillars on the same grid as the main analysis.

Work      = log1p( sum_j headcount_j   * 2^(-t_ij / hl_work) )         offices + SEZ soft boost
Lifestyle = per category c: log1p( sum_j weight_jc * 2^(-t_ij / hl_life) ) ; combined in the viewer by sliders
Metro     = 0.65 * 2^(-t_station/10)  +  0.35 * (1 - exp(-rel / 0.10))
            rel  = (jobs within H min by drive+metro - jobs within H min by drive only) / (drive-only jobs + 1000)
            -- the "metro premium" (H = job_horizon_min, default 45)
Times are PEAK-HOUR (road speeds x peak_speed_factor); metro is unaffected by traffic.
Scores are percentiles against the first year's distribution so growth can exceed the baseline
(the metro pillar is already on a fixed 0-1 scale).
"""
from __future__ import annotations

import csv
import json
from pathlib import Path

import numpy as np
from scipy.sparse.csgraph import dijkstra
from scipy.spatial import cKDTree

from . import embed, metrics, metro as metro_mod, router
from .osm_scan import LIFESTYLE_CATEGORIES

PERSONAS = {  # default pillar weights; the viewer lets the user move them
    "buyer": {"label": "Buyer who lives there", "work": 0.4, "lifestyle": 0.3, "metro": 0.3},
    "investor": {"label": "Rental-yield investor", "work": 0.5, "lifestyle": 0.2, "metro": 0.3},
    "commercial": {"label": "Commercial / retail", "work": 0.4, "lifestyle": 0.4, "metro": 0.2},
}
CATEGORY_DEFAULTS = {"dining": 1.0, "nightlife": 0.5, "malls": 1.0, "cinemas": 0.5, "parks": 0.7}
SEZ_SECTOR_SCALE = {  # share of `sez_mass` a SEZ of this sector group is assumed to add (assumption, editable)
    "IT / ITES & software": 1.0, "Electronics & semiconductors": 0.6, "Engineering, aerospace & auto": 0.5,
    "Pharma, biotech & chemicals": 0.5, "Multi-product, multi-sector & port": 0.5,
    "Textile, apparel & footwear": 0.3, "Gems, food & other niche": 0.3, "Energy, metals & minerals": 0.3,
}


def bin_weights(xy_pts, weights, grid_xy, cell):
    """Sum weights of points falling in each (cell-sized) grid cell."""
    x0, y0 = grid_xy[:, 0].min() - cell[0] / 2, grid_xy[:, 1].min() - cell[1] / 2
    nx_ = int(round((grid_xy[:, 0].max() - grid_xy[:, 0].min()) / cell[0])) + 1
    ny_ = int(round((grid_xy[:, 1].max() - grid_xy[:, 1].min()) / cell[1])) + 1
    out = np.zeros((ny_, nx_))
    if len(xy_pts):
        xy_pts = np.asarray(xy_pts, float)
        ix = np.floor((xy_pts[:, 0] - x0) / cell[0]).astype(int)
        iy = np.floor((xy_pts[:, 1] - y0) / cell[1]).astype(int)
        ok = (ix >= 0) & (ix < nx_) & (iy >= 0) & (iy < ny_)
        np.add.at(out, (iy[ok], ix[ok]), np.asarray(weights, float)[ok])
    return out.ravel()


def sez_boost(csv_path, proj, grid_xy, bbox_pad_km=3.0, sigma_km=2.0, mass=3000.0):
    """Spread each SEZ's mass over nearby grid cells (Gaussian), preserving the mass that lands on the grid."""
    out = np.zeros(len(grid_xy))
    used = []
    lo, hi = grid_xy.min(0) - bbox_pad_km, grid_xy.max(0) + bbox_pad_km
    with open(csv_path, encoding="utf-8-sig") as fh:
        for r in csv.DictReader(fh):
            try:
                x, y = proj.fwd(float(r["Longitude_approx"]), float(r["Latitude_approx"]))
            except (ValueError, KeyError):
                continue
            if not (lo[0] <= x <= hi[0] and lo[1] <= y <= hi[1]):
                continue
            m = mass * SEZ_SECTOR_SCALE.get(r.get("Sector_group", ""), 0.3)
            g = np.exp(-(((grid_xy - [x, y]) ** 2).sum(1)) / (2 * sigma_km ** 2))
            if g.sum() > 0:
                out += m * g / g.sum()
                used.append(r.get("Developer", "?"))
    return out, used


def gravity(minutes, weights, half_life):
    return np.log1p(np.power(2.0, -minutes / half_life) @ weights)


def composite(pillars: dict, weights: dict):
    """Weighted geometric mean over pillars with positive weight; pillars absent for a city drop out."""
    keys = [k for k, w in weights.items() if w > 0 and k in pillars]
    tot = sum(weights[k] for k in keys)
    if not keys:
        return np.zeros_like(next(iter(pillars.values())))
    eps = 0.02
    return np.exp(sum(weights[k] / tot * np.log(np.clip(pillars[k], eps, 1.0)) for k in keys))


def lifestyle_score(cat_pct: dict, cat_w: dict):
    keys = [c for c in cat_pct if cat_w.get(c, 0) > 0]
    tot = sum(cat_w[c] for c in keys)
    return sum(cat_w[c] * cat_pct[c] for c in keys) / tot if keys else np.zeros(len(next(iter(cat_pct.values()))))


def pct_vs(ref, v):
    ref = np.sort(ref)
    return np.searchsorted(ref, v, side="right") / max(len(ref), 1)


def build(key, cfg, proj, grid_xy, cell, nx_, ny_, valid, v_ref, lattice, core, pad, scan, out_dir: Path, out_csv: Path,
          root: Path):
    pc = cfg["proximity"]
    pf = float(pc.get("peak_speed_factor", 0.6))
    hl_w, hl_l = float(pc.get("half_life_work", 15)), float(pc.get("half_life_lifestyle", 10))
    n = len(grid_xy)
    vi = np.flatnonzero(valid)

    # ---- opportunities per cell
    pts = [(*proj.fwd(lon, lat), w) for lon, lat, w in scan.offices]
    office = bin_weights([(p[0], p[1]) for p in pts], [p[2] for p in pts], grid_xy, cell)
    sez, used = sez_boost(root / pc["sez_csv"], proj, grid_xy, mass=float(pc.get("sez_mass", 3000))) \
        if pc.get("sez_csv") else (np.zeros(n), [])
    work_w = office + sez
    print(f"[{key}] offices: {len(pts)} OSM ({office.sum():.0f} headcount proxy) + {len(used)} SEZs ({sez.sum():.0f})")
    cat_w = {}
    for cat in LIFESTYLE_CATEGORIES:
        sel = [(c, lon, lat, w) for c, lon, lat, w in scan.lifestyle if c == cat]
        xy = [proj.fwd(lon, lat) for _, lon, lat, _ in sel]
        cat_w[cat] = bin_weights([(a, b) for a, b in xy], [w for *_, w in sel], grid_xy, cell)
    print(f"[{key}] lifestyle: " + ", ".join(f"{c} {int(round(v.sum()))}" for c, v in cat_w.items()))

    # ---- graphs and matrices
    road = router.build_graph(scan, proj, {c: pf for c in router.SPEED_KMH})
    tree = cKDTree(road.xy)
    snap_km, snap_node = tree.query(grid_xy)
    M = metro_mod.load_metro(root / pc["metro_file"], scan, proj)
    print(f"[{key}] metro: " + "; ".join(f"{l.name}: {len(l.order)} stations, opens {l.opened}" for l in M.lines))
    T0 = router.od_minutes(road.g, snap_node, snap_km)           # drive only, peak
    years = pc["years"]

    def fit(T, X_prev):
        S = embed.symmetrise(T)
        sub = (S * v_ref / 60.0)[np.ix_(vi, vi)]
        G = grid_xy[vi]
        X, _ = embed.weighted_smacof(sub, G if X_prev is None else X_prev, alpha=1.0)
        X = embed.rigid_align(X, G)
        f = embed.fit_field(G, X, lattice, core, pad)
        return f, X

    fields = []
    f0, X_prev = fit(T0, None)
    fields.append(f0)
    snaps = []
    base = {}
    jobs_idx = np.arange(n)
    for yi, y in enumerate(years):
        g, hubs, Nroad = metro_mod.build_union(road.g, road.xy, M, int(y["year"]), with_ride=True)
        Tm = router.od_minutes(g, snap_node, snap_km)
        fy, X_prev = fit(Tm, X_prev)
        fields.append(fy)
        # station access (road + access edges only, no ride)
        ga, hubs_a, _ = metro_mod.build_union(road.g, road.xy, M, int(y["year"]), with_ride=False)
        if hubs_a:
            d = dijkstra(ga.T.tocsr(), directed=True, indices=hubs_a)[:, snap_node] + \
                (snap_km / router.ACCESS_SPEED_KMH * 3600.0)[None, :]
            d_min = d / 60.0
            t_stn, stn15 = d_min.min(0), (d_min <= 15).sum(0)
        else:
            t_stn, stn15 = np.full(n, np.inf), np.zeros(n, int)
        acc_w_mm, acc_w_drive = gravity(Tm, work_w, hl_w), gravity(T0, work_w, hl_w)
        gain = np.maximum(acc_w_mm - acc_w_drive, 0.0)
        H = float(pc.get("job_horizon_min", 45))
        j30_mm, j30_d = ((Tm <= H) * work_w[None, :]).sum(1), ((T0 <= H) * work_w[None, :]).sum(1)
        rel = np.maximum(j30_mm - j30_d, 0.0) / (j30_d + 1000.0)
        life = {c: gravity(Tm, w, hl_l) for c, w in cat_w.items()}
        if yi == 0:
            base = {"work": acc_w_mm[valid].copy(), **{c: life[c][valid].copy() for c in life}}
        pillars = {"work": pct_vs(base["work"], acc_w_mm),
                   "life": {c: pct_vs(base[c], life[c]) for c in life},
                   "metro": 0.65 * np.where(np.isfinite(t_stn), np.power(2.0, -np.where(np.isfinite(t_stn), t_stn, 0) / 10), 0)
                   + 0.35 * (1 - np.exp(-rel / 0.10))}
        snaps.append({"year": y, "pillars": pillars, "t_stn": t_stn, "stn15": stn15, "gain": gain,
                      "j30_mm": j30_mm, "j30_d": j30_d, "rel": rel, "median_trip": float(np.median(Tm[np.ix_(vi, vi)])),
                      "active": sorted(M.active(int(y["year"])).keys())})
        print(f"[{key}] {y['id']}: stations active {len(hubs)}, mean metro score {pillars['metro'][valid].mean():.3f}, "
              f"mean premium {gain[valid].mean():.3f}, fold-free alpha {fy['alpha']:.2f} minJ {fy['min_j']:.2f}")

    # ---- export
    out_dir.mkdir(parents=True, exist_ok=True)
    r3 = lambda a: np.round(np.nan_to_num(np.asarray(a, float), nan=0.0, posinf=999.0), 3).tolist()
    export = {
        "nx": nx_, "ny": ny_, "personas": PERSONAS, "category_defaults": CATEGORY_DEFAULTS,
        "categories": LIFESTYLE_CATEGORIES, "peak_speed_factor": pf,
        "half_life": {"work": hl_w, "lifestyle": hl_l}, "job_horizon_min": float(pc.get("job_horizon_min", 45)),
        "assumptions": {"sez_mass": float(pc.get("sez_mass", 3000)), "sez_sigma_km": 2.0, "sez_count": len(used)},
        "years": [{"id": s["year"]["id"], "label": s["year"]["label"], "year": s["year"]["year"],
                   "scenario": bool(s["year"].get("scenario")), "median_trip_min": s["median_trip"],
                   "lines_active": s["active"]} for s in snaps],
        "work": [r3(s["pillars"]["work"]) for s in snaps],
        "life": {c: [r3(s["pillars"]["life"][c]) for s in snaps] for c in LIFESTYLE_CATEGORIES},
        "metro": [r3(s["pillars"]["metro"]) for s in snaps],
        "t_station": [r3(np.minimum(s["t_stn"], 999)) for s in snaps],
        "stn15": [s["stn15"].astype(int).tolist() for s in snaps],
        "gain": [r3(s["gain"]) for s in snaps], "rel_gain": [r3(s["rel"]) for s in snaps],
        "jobs30": [r3(s["j30_mm"]) for s in snaps], "jobs30_drive": [r3(s["j30_d"]) for s in snaps],
        "office_mass": r3(work_w),
        "lines": [{"id": l.id, "name": l.name, "color": l.color, "status": l.status, "opened": l.opened,
                   "ways": [np.round(w.ravel(), 3).tolist() for w in l.ways],
                   "stations": [{"n": M.hubs[h]["name"], "x": round(M.hubs[h]["x"], 3), "y": round(M.hubs[h]["y"], 3)}
                                for h in l.order]} for l in M.lines],
        "fields": {"nx": lattice.nx, "ny": lattice.ny, "x0": lattice.x0, "y0": lattice.y0, "step": lattice.step,
                   "dx": [r3(f["displacement"][:, 0]) for f in fields], "dy": [r3(f["displacement"][:, 1]) for f in fields]},
    }
    (out_dir / "proximity.json").write_text(json.dumps(export, separators=(",", ":")))

    # ---- ranked CSV per persona (latest non-scenario year, change vs first year)
    last = max((i for i, s in enumerate(snaps) if not s["year"].get("scenario")), default=len(snaps) - 1)
    rows = []
    for pk, pw in PERSONAS.items():
        def score(i):
            p = snaps[i]["pillars"]
            pil = {"work": p["work"], "lifestyle": lifestyle_score(p["life"], CATEGORY_DEFAULTS), "metro": p["metro"]}
            return composite(pil, {k: pw[k] for k in ("work", "lifestyle", "metro")}), pil
        sc, pil = score(last)
        sc0, _ = score(0)
        thr = float(np.quantile(sc[valid], float(pc.get("cluster_quantile", 0.85))))
        cl = metrics.clusters(sc, valid, (ny_, nx_), thr, 3)
        for cid in sorted(set(cl[cl > 0].tolist())):
            m = cl == cid
            cx, cy = grid_xy[m].mean(0)
            near = sorted((np.hypot(px - cx, py - cy), nm) for nm, rk, lo, la in scan.places if rk in (1, 2)
                          for px, py in [proj.fwd(lo, la)] if np.hypot(px - cx, py - cy) < 3.0)[:3]
            lon, lat = proj.inv(cx, cy)
            rows.append({"persona": pk, "cluster": cid, "areas": "; ".join(nm for _, nm in near) or "(unnamed)",
                         "cells": int(m.sum()), "area_km2": round(m.sum() * cell[0] * cell[1], 1),
                         "lat": round(float(lat), 5), "lon": round(float(lon), 5),
                         "score": round(float(sc[m].mean()), 3), "delta_vs_first": round(float((sc[m] - sc0[m]).mean()), 3),
                         "work": round(float(pil["work"][m].mean()), 3), "lifestyle": round(float(pil["lifestyle"][m].mean()), 3),
                         "metro": round(float(pil["metro"][m].mean()), 3), "year": snaps[last]["year"]["label"]})
    rows.sort(key=lambda r: (r["persona"], -r["score"]))
    out_csv.mkdir(parents=True, exist_ok=True)
    with open(out_csv / f"{key}_proximity.csv", "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0].keys()) if rows else ["persona"])
        w.writeheader(); w.writerows(rows)
    print(f"[{key}] proximity: {len(rows)} persona clusters -> {out_csv / (key + '_proximity.csv')}")
