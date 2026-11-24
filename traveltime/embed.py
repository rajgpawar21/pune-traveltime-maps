"""Fixed-scale weighted MDS and a smooth, fold-checked warp field.

Design choices that differ from the first version of this project:
* Distances are converted to km at a FIXED reference speed, so every snapshot
  lives in the same units. Changes between snapshots are therefore real, not an
  artefact of per-run min-max rescaling.
* Stress is weighted by 1/d so local geometry (what a map reader actually sees)
  is preserved better than far-apart pairs.
* The map is a thin-plate-spline displacement field, not a raster warp. The
  field is checked for folds (negative Jacobian) and smoothed until none remain.
"""
from __future__ import annotations

import numpy as np
from scipy.interpolate import RBFInterpolator
from scipy.stats import spearmanr


def symmetrise(minutes: np.ndarray) -> np.ndarray:
    a = np.asarray(minutes, float)
    b = a.T
    out = np.where(np.isfinite(a) & np.isfinite(b), (a + b) / 2, np.where(np.isfinite(a), a, b))
    if not np.isfinite(out).all():
        raise ValueError("Travel-time matrix has pairs unreachable in both directions.")
    np.fill_diagonal(out, 0.0)
    return out


def pairwise(X):
    d = X[:, None, :] - X[None, :, :]
    return np.sqrt((d ** 2).sum(-1))


def weighted_smacof(D, X0, alpha=1.0, max_iter=400, tol=1e-7):
    """Stress majorisation with weights w_ij = d_ij^-alpha (local emphasis)."""
    n = len(D)
    with np.errstate(divide="ignore"):
        W = np.where(D > 0, D ** (-alpha), 0.0)
    V = -W.copy()
    V[np.diag_indices(n)] = W.sum(1)
    Vp = np.linalg.pinv(V)
    X = X0.copy()

    def stress(Xc):
        return float((W * (pairwise(Xc) - D) ** 2).sum() / 2)

    prev = stress(X)
    for _ in range(max_iter):
        dist = pairwise(X)
        ratio = np.divide(D, dist, out=np.zeros_like(D), where=dist > 1e-9)
        B = -W * ratio
        B[np.diag_indices(n)] = 0.0
        B[np.diag_indices(n)] = -B.sum(1)
        X = Vp @ (B @ X)
        cur = stress(X)
        if prev - cur < tol * max(prev, 1e-12):
            prev = cur
            break
        prev = cur
    return X, prev


def rigid_align(X, Y):
    """Rotate/translate X onto Y (no scaling, no reflection)."""
    mx, my = X.mean(0), Y.mean(0)
    U, _, Vt = np.linalg.svd((X - mx).T @ (Y - my))
    d = np.sign(np.linalg.det(U @ Vt))
    R = U @ np.diag([1, d]) @ Vt
    return (X - mx) @ R + my


class Lattice:
    def __init__(self, extent, step):
        xmin, ymin, xmax, ymax = extent
        self.nx = int(np.ceil((xmax - xmin) / step)) + 1
        self.ny = int(np.ceil((ymax - ymin) / step)) + 1
        self.x0, self.y0, self.step = xmin, ymin, step
        xs = self.x0 + np.arange(self.nx) * step
        ys = self.y0 + np.arange(self.ny) * step
        gx, gy = np.meshgrid(xs, ys)
        self.xy = np.column_stack((gx.ravel(), gy.ravel()))
        self.shape = (self.ny, self.nx)

    def jacobian(self, warped):
        wx = warped[:, 0].reshape(self.shape)
        wy = warped[:, 1].reshape(self.shape)
        dwx_dy, dwx_dx = np.gradient(wx, self.step)
        dwy_dy, dwy_dx = np.gradient(wy, self.step)
        return (dwx_dx * dwy_dy - dwx_dy * dwy_dx)


def _pad_ring(extent, k=14):
    xmin, ymin, xmax, ymax = extent
    t = np.linspace(0, 1, k, endpoint=False)
    xs, ys = xmin + (xmax - xmin) * t, ymin + (ymax - ymin) * t
    return np.vstack([
        np.column_stack((xs, np.full(k, ymin))), np.column_stack((np.full(k, xmax), ys)),
        np.column_stack((xs[::-1] + (xmax - xmin) / k, np.full(k, ymax))),
        np.column_stack((np.full(k, xmin), ys[::-1] + (ymax - ymin) / k)),
    ])


def fit_field(G, X, lattice: Lattice, core_extent, pad_extent,
              smoothing_grid=(0.0, 0.05, 0.3, 1.5, 6.0, 25.0, 100.0), min_jacobian=0.2):
    """Return dict(displacement (N,2), jacobian (ny,nx), smoothing, alpha, min_j)."""
    pad = _pad_ring(pad_extent)
    nodes = np.vstack((G, pad))
    targets = np.vstack((X, pad))
    cx0, cy0, cx1, cy1 = core_extent
    inner = ((lattice.xy[:, 0] >= cx0) & (lattice.xy[:, 0] <= cx1) &
             (lattice.xy[:, 1] >= cy0) & (lattice.xy[:, 1] <= cy1)).reshape(lattice.shape)

    chosen = None
    for lam in smoothing_grid:
        rbf = RBFInterpolator(nodes, targets, kernel="thin_plate_spline", smoothing=lam)
        warped = rbf(lattice.xy)
        J = lattice.jacobian(warped)
        min_j = float(J[inner].min())
        chosen = (lam, 1.0, warped, J, min_j)
        if min_j >= min_jacobian:
            break
    lam, alpha, warped, J, min_j = chosen
    if min_j < min_jacobian:  # last resort: shrink displacement until fold-free
        disp_full = warped - lattice.xy
        lo, hi = 0.0, 1.0
        for _ in range(14):
            mid = (lo + hi) / 2
            Jm = lattice.jacobian(lattice.xy + mid * disp_full)
            if Jm[inner].min() >= min_jacobian:
                lo = mid
            else:
                hi = mid
        alpha = lo
        warped = lattice.xy + alpha * disp_full
        J = lattice.jacobian(warped)
        min_j = float(J[inner].min())
    return {"displacement": warped - lattice.xy, "jacobian": J, "smoothing": float(lam),
            "alpha": float(alpha), "min_j": min_j}


def apply_field(lattice: Lattice, displacement, pts):
    """Bilinear sample of the displacement field at pts (n,2)."""
    dx = displacement[:, 0].reshape(lattice.shape)
    dy = displacement[:, 1].reshape(lattice.shape)
    fx = np.clip((pts[:, 0] - lattice.x0) / lattice.step, 0, lattice.nx - 1.001)
    fy = np.clip((pts[:, 1] - lattice.y0) / lattice.step, 0, lattice.ny - 1.001)
    ix, iy = fx.astype(int), fy.astype(int)
    tx, ty = fx - ix, fy - iy

    def samp(a):
        return (a[iy, ix] * (1 - tx) * (1 - ty) + a[iy, ix + 1] * tx * (1 - ty) +
                a[iy + 1, ix] * (1 - tx) * ty + a[iy + 1, ix + 1] * tx * ty)
    return pts + np.column_stack((samp(dx), samp(dy)))


def fidelity(Xmap, D, Xgeo, bands=((0, 3), (3, 8), (8, 99))):
    """Honest scorecard: does the map beat plain geography at reproducing travel times?

    Compares median relative error of map distances vs. travel-time km, per
    distance band, for the warped map and for the untouched geographic layout.
    """
    iu = np.triu_indices(len(D), 1)
    dt = D[iu]
    dm, dg = pairwise(Xmap)[iu], pairwise(Xgeo)[iu]
    out = {"spearman_map": float(spearmanr(dm, dt)[0]), "spearman_geo": float(spearmanr(dg, dt)[0]),
           "bands_km": []}
    for lo, hi in bands:
        m = (dt >= max(lo, 0.5)) & (dt < hi)
        if m.sum() < 20:
            continue
        out["bands_km"].append({
            "band": f"{lo}-{hi if hi < 99 else '+'}",
            "err_map": float(np.median(np.abs(dm[m] - dt[m]) / dt[m])),
            "err_geo": float(np.median(np.abs(dg[m] - dt[m]) / dt[m])),
        })
    return out
