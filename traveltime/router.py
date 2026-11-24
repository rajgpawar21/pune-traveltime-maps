"""Travel-time matrices: a built-in free-flow router, or an OSRM server.

The built-in router needs no Docker and reads the same PBF the viewer uses, so
every historical snapshot can be routed identically (and in CI). It models
free-flow speeds by road class plus a junction delay; it is NOT traffic-aware.
Use OSRM (or a traffic API matrix dropped in as .npy) when you need to match a
specific routing engine.
"""
from __future__ import annotations

import os
import time

import numpy as np
import requests
from scipy.sparse import coo_matrix, csr_matrix
from scipy.sparse.csgraph import connected_components, dijkstra
from scipy.spatial import cKDTree

from .geo import Projection

# Free-flow urban speeds (km/h). Deliberately conservative for Indian cities.
SPEED_KMH = {
    "motorway": 60, "trunk": 45, "primary": 35, "secondary": 28, "tertiary": 24,
    "unclassified": 20, "residential": 18, "living_street": 10, "service": 10,
}
LINK_FACTOR = 0.8
JUNCTION_DELAY_S = 4.0
ACCESS_SPEED_KMH = 15.0  # grid point -> nearest road node


def road_speed(cls: str, scale: dict | None = None) -> float:
    base = cls.replace("_link", "")
    v = SPEED_KMH.get(base, 15.0) * (LINK_FACTOR if cls.endswith("_link") else 1.0)
    if scale:
        v *= float(scale.get(base, 1.0))
    return v


class Graph:
    def __init__(self, xy, indptr_graph: csr_matrix):
        self.xy = xy
        self.g = indptr_graph


def build_graph(scan, proj: Projection, speed_scale: dict | None = None) -> Graph:
    roads = scan.roads
    all_ids = np.concatenate([r[2] for r in roads])
    uniq, counts = np.unique(all_ids, return_counts=True)
    shared = set(uniq[counts >= 2].tolist())
    index, xy_list = {}, []
    src, dst, cost = [], [], []

    def node(nid, x, y):
        i = index.get(nid)
        if i is None:
            i = len(xy_list)
            index[nid] = i
            xy_list.append((x, y))
        return i

    for cls, oneway, ids, ll in roads:
        x, y = proj.fwd(ll[:, 0], ll[:, 1])
        seg = np.hypot(np.diff(x), np.diff(y))
        cum = np.concatenate(([0.0], np.cumsum(seg)))
        keep = [0] + [k for k in range(1, len(ids) - 1) if int(ids[k]) in shared] + [len(ids) - 1]
        speed = road_speed(cls, speed_scale)
        for a, b in zip(keep[:-1], keep[1:]):
            if ids[a] == ids[b]:
                continue
            ia = node(int(ids[a]), x[a], y[a])
            ib = node(int(ids[b]), x[b], y[b])
            t = (cum[b] - cum[a]) / speed * 3600.0 + JUNCTION_DELAY_S
            if oneway >= 0:
                src.append(ia); dst.append(ib); cost.append(t)
            if oneway <= 0:
                src.append(ib); dst.append(ia); cost.append(t)

    n = len(xy_list)
    src, dst, cost = np.array(src), np.array(dst), np.array(cost)
    # keep the cheapest of any parallel edges
    key = src.astype(np.int64) * n + dst
    order = np.argsort(key, kind="stable")
    key, cost, src, dst = key[order], cost[order], src[order], dst[order]
    first = np.concatenate(([True], key[1:] != key[:-1]))
    starts = np.flatnonzero(first)
    cost_min = np.minimum.reduceat(cost, starts)
    g = coo_matrix((cost_min, (src[starts], dst[starts])), shape=(n, n)).tocsr()

    # restrict to the largest strongly connected component
    _, labels = connected_components(g, directed=True, connection="strong")
    big = np.bincount(labels).argmax()
    keep_nodes = np.flatnonzero(labels == big)
    g = g[keep_nodes][:, keep_nodes]
    xy = np.array(xy_list)[keep_nodes]
    return Graph(xy, g.tocsr())


def builtin_matrix(scan, proj: Projection, grid_xy, speed_scale=None, off_network_km=1.5):
    """Return (minutes matrix n x n, snap distance km per point)."""
    graph = build_graph(scan, proj, speed_scale)
    tree = cKDTree(graph.xy)
    snap_km, snap_node = tree.query(grid_xy)
    access_s = snap_km / ACCESS_SPEED_KMH * 3600.0
    n = len(grid_xy)
    out = np.empty((n, n), dtype=float)
    uniq_nodes, inverse = np.unique(snap_node, return_inverse=True)
    for lo in range(0, len(uniq_nodes), 64):
        chunk = uniq_nodes[lo:lo + 64]
        d = dijkstra(graph.g, directed=True, indices=chunk)[:, uniq_nodes]
        for row, node_id in enumerate(chunk):
            members = np.flatnonzero(snap_node == node_id)
            out[np.ix_(members, np.arange(n))] = d[row][inverse][None, :]
    out = out + access_s[:, None] + access_s[None, :]
    np.fill_diagonal(out, 0.0)
    return out / 60.0, snap_km


def osrm_matrix(grid_lonlat, url=None, profile="driving", block=50, timeout=120):
    """Minutes matrix from an OSRM server, chunked to respect --max-table-size."""
    url = (url or os.environ.get("OSRM_URL", "http://localhost:5001")).rstrip("/")
    n = len(grid_lonlat)
    out = np.full((n, n), np.nan)
    for i0 in range(0, n, block):
        for j0 in range(0, n, block):
            I = list(range(i0, min(i0 + block, n)))
            J = list(range(j0, min(j0 + block, n)))
            pts = [grid_lonlat[k] for k in I] + [grid_lonlat[k] for k in J]
            coords = ";".join(f"{lon:.6f},{lat:.6f}" for lon, lat in pts)
            params = {"annotations": "duration",
                      "sources": ";".join(str(k) for k in range(len(I))),
                      "destinations": ";".join(str(len(I) + k) for k in range(len(J)))}
            for attempt in range(3):
                try:
                    r = requests.get(f"{url}/table/v1/{profile}/{coords}", params=params, timeout=timeout)
                    r.raise_for_status()
                    payload = r.json()
                    if payload.get("code") != "Ok":
                        raise RuntimeError(payload.get("message", payload.get("code")))
                    out[np.ix_(I, J)] = np.array(payload["durations"], dtype=float, copy=True)
                    break
                except (requests.RequestException, RuntimeError):
                    if attempt == 2:
                        raise
                    time.sleep(2 ** attempt)
    np.fill_diagonal(out, 0.0)
    return out / 60.0


def od_minutes(g, snap_node, snap_km):
    """Minutes OD matrix on any graph whose first nodes are road nodes (snap_node indexes them)."""
    n = len(snap_node)
    uniq, inv = np.unique(snap_node, return_inverse=True)
    block = np.empty((len(uniq), len(uniq)))
    for lo in range(0, len(uniq), 64):
        d = dijkstra(g, directed=True, indices=uniq[lo:lo + 64])
        block[lo:lo + 64] = d[:, uniq]
    access_s = snap_km / ACCESS_SPEED_KMH * 3600.0
    out = block[np.ix_(inv, inv)] + access_s[:, None] + access_s[None, :]
    np.fill_diagonal(out, 0.0)
    return out / 60.0
