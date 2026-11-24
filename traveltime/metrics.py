"""Activity, gravity accessibility, micro-market score and clusters.

micro-market score  M = sqrt( pct(activity) * pct(accessibility to OTHER active cells) )
Percentiles are taken against the FIRST snapshot's distribution so scores are
comparable over time (a rank-within-each-snapshot score could never show growth).
"""
from __future__ import annotations

import numpy as np

from .geo import Projection


def activity_counts(scan, proj: Projection, grid_xy, cell_km):
    """POIs and buildings per grid cell (cell is a cell_km square around the point)."""
    out = {}
    for name, pts in (("poi", scan.poi), ("buildings", scan.buildings)):
        a = np.array(pts, float).reshape(-1, 2)
        x, y = proj.fwd(a[:, 0], a[:, 1])
        ix = np.floor((x - (grid_xy[:, 0].min() - cell_km[0] / 2)) / cell_km[0]).astype(int)
        iy = np.floor((y - (grid_xy[:, 1].min() - cell_km[1] / 2)) / cell_km[1]).astype(int)
        nx_ = int(round((grid_xy[:, 0].max() - grid_xy[:, 0].min()) / cell_km[0])) + 1
        ny_ = int(round((grid_xy[:, 1].max() - grid_xy[:, 1].min()) / cell_km[1])) + 1
        ok = (ix >= 0) & (ix < nx_) & (iy >= 0) & (iy < ny_)
        counts = np.zeros((ny_, nx_))
        np.add.at(counts, (iy[ok], ix[ok]), 1)
        out[name] = counts.ravel()
    return out


def raw_activity(counts, building_weight=0.1):
    return counts["poi"] + building_weight * counts["buildings"]


def gravity_accessibility(minutes_sym, raw, half_life_min=10.0, valid=None):
    """log1p( sum_j raw_j * 2^(-t_ij/half_life) ), excluding the cell itself."""
    w = raw.copy()
    if valid is not None:
        w = np.where(valid, w, 0.0)
    decay = np.power(2.0, -minutes_sym / half_life_min)
    np.fill_diagonal(decay, 0.0)
    return np.log1p(decay @ w)


def percentile_vs(reference, values):
    ref = np.sort(reference)
    return np.searchsorted(ref, values, side="right") / max(len(ref), 1)


def friction(D_km, geo_xy, radius_km=3.0):
    """Mean (reference-speed km of travel) per geographic km, over near neighbours."""
    g = np.sqrt(((geo_xy[:, None] - geo_xy[None]) ** 2).sum(-1))
    out = np.full(len(D_km), np.nan)
    for i in range(len(D_km)):
        m = (g[i] > 0.01) & (g[i] <= radius_km)
        if m.any():
            out[i] = np.mean(D_km[i, m] / g[i, m])
    return out


def clusters(score, valid, shape, threshold=0.75, min_cells=3):
    """Contiguous (8-neighbour) blocks of high-score cells = micro-markets.

    Contiguity keeps micro-markets compact. Connectivity BETWEEN micro-markets
    is already inside the score (accessibility to other active cells).
    """
    from scipy import ndimage
    mask = (valid & (score >= threshold)).reshape(shape)
    lab, n = ndimage.label(mask, structure=np.ones((3, 3)))
    sizes = ndimage.sum(mask, lab, index=np.arange(1, n + 1))
    order = [i + 1 for i in np.argsort(-sizes) if sizes[i] >= min_cells]
    out = np.zeros(lab.shape, dtype=int)
    for new_id, old in enumerate(order, start=1):
        out[lab == old] = new_id
    return out.ravel()
