"""Metro lines from OSM geometry + a curated YAML, and a door-to-door multimodal graph.

Network model (all times in seconds):
    road node --access--> station hub --board wait--> platform(line) --ride--> platform(line) --0--> hub --access--> road node
A hub is shared by every line that serves it, so a transfer costs one more board wait.
"""
from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np
import shapely
import yaml
from scipy.sparse import coo_matrix
from scipy.spatial import cKDTree

from .router import ACCESS_SPEED_KMH

RIDE_KMH = 32.0
DWELL_S = 20.0
BOARD_WAIT_S = 300.0       # average wait at ~10 min headway
STATION_MERGE_KM = 0.3
SAME_NAME_MERGE_KM = 1.0
LINE_ATTACH_KM = 0.35
RIDE_CIRCUITY = 1.1


@dataclass
class Line:
    id: str
    name: str
    color: str
    status: str
    opened: int
    ways: list = field(default_factory=list)        # list of (n,2) km arrays
    order: list = field(default_factory=list)       # hub indices along the line
    station_open: dict = field(default_factory=dict)


@dataclass
class Metro:
    lines: list
    hubs: list          # dicts {name, x, y, lines:[line index]}

    def active(self, year: int):
        """Return {line_idx: set(hub_idx) in service} for a given year."""
        out = {}
        for li, ln in enumerate(self.lines):
            if ln.opened > year:
                continue
            hubs = {h for h in ln.order if ln.station_open.get(self.hubs[h]["name"], ln.opened) <= year}
            if len(hubs) >= 2:
                out[li] = hubs
        return out


def chain_order(xy):
    """Order stations along a (roughly linear) line: nearest-neighbour from one extreme end."""
    xy = np.asarray(xy, float)
    if len(xy) <= 2:
        return list(range(len(xy)))
    c = xy.mean(0)
    a = int(np.argmax(((xy - c) ** 2).sum(1)))
    b = int(np.argmax(((xy - xy[a]) ** 2).sum(1)))
    left, order, cur = set(range(len(xy))) - {b}, [b], b
    while left:
        nxt = min(left, key=lambda j: ((xy[j] - xy[cur]) ** 2).sum())
        order.append(nxt); left.remove(nxt); cur = nxt
    return order


def load_metro(yaml_path, scan, proj) -> Metro:
    cfg = yaml.safe_load(open(yaml_path))
    lines = [Line(l["id"], l["name"], l.get("color", "#2ec4b6"), l.get("status", "open"), int(l["opened"]),
                  station_open={k: int(v) for k, v in (l.get("station_open") or {}).items()})
             for l in cfg["lines"]]
    matches = [[m.lower() for m in l.get("match", [])] for l in cfg["lines"]]
    for name, _status, ll in scan.metro_ways:   # OSM status deliberately ignored
        low = name.lower()
        for li, ms in enumerate(matches):
            if low and any(m in low for m in ms):
                x, y = proj.fwd(ll[:, 0], ll[:, 1])
                lines[li].ways.append(np.column_stack((x, y)))
                break
    # cluster station nodes into hubs (union within STATION_MERGE_KM)
    pts = []
    for nm, lon, lat in scan.metro_nodes:
        x, y = proj.fwd(lon, lat)
        pts.append((nm, float(x), float(y)))
    parent = list(range(len(pts)))

    def find(i):
        while parent[i] != i:
            parent[i] = parent[parent[i]]; i = parent[i]
        return i
    if pts:
        arr = np.array([(p[1], p[2]) for p in pts])
        for i, j in cKDTree(arr).query_pairs(STATION_MERGE_KM):
            parent[find(i)] = find(j)
        norm = lambda n: "".join(ch for ch in n.lower() if ch.isalnum())
        for i, j in cKDTree(arr).query_pairs(SAME_NAME_MERGE_KM):
            if pts[i][0] != "Station" and norm(pts[i][0]) == norm(pts[j][0]):
                parent[find(i)] = find(j)
    groups = {}
    for i in range(len(pts)):
        groups.setdefault(find(i), []).append(i)
    hubs = []
    for idx in groups.values():
        names = [pts[i][0] for i in idx if pts[i][0] != "Station"]
        hubs.append({"name": names[0] if names else "Station",
                     "x": float(np.mean([pts[i][1] for i in idx])), "y": float(np.mean([pts[i][2] for i in idx])),
                     "lines": []})
    geoms = [shapely.union_all([shapely.linestrings(w) for w in ln.ways]) if ln.ways else None for ln in lines]
    for hi, h in enumerate(hubs):
        p = shapely.points(h["x"], h["y"])
        for li, g in enumerate(geoms):
            if g is not None and shapely.distance(g, p) <= LINE_ATTACH_KM:
                h["lines"].append(li)
    hubs = [h for h in hubs if h["lines"]]
    for li, ln in enumerate(lines):
        members = [hi for hi, h in enumerate(hubs) if li in h["lines"]]
        if members:
            order = chain_order([(hubs[m]["x"], hubs[m]["y"]) for m in members])
            ln.order = [members[o] for o in order]
    return Metro(lines, hubs)


def build_union(base_g, road_xy, metro: Metro, year: int, with_ride=True):
    """Return (csr graph, hub node ids of active hubs, node count N_road)."""
    N, H = base_g.shape[0], len(metro.hubs)
    act = metro.active(year)
    active_hubs = sorted(set().union(*act.values())) if act else []
    plat = {}
    for li, hs in act.items():
        for h in sorted(hs):
            plat[(li, h)] = N + H + len(plat)
    total = N + H + len(plat)
    tree = cKDTree(road_xy)
    src, dst, cost = [], [], []
    for h in active_hubs:
        d, r = tree.query([metro.hubs[h]["x"], metro.hubs[h]["y"]])
        t = d / ACCESS_SPEED_KMH * 3600.0 + 30.0
        src += [r, N + h]; dst += [N + h, r]; cost += [t, t]
    if with_ride:
        for li, hs in act.items():
            ln = metro.lines[li]
            seq = [h for h in ln.order if h in hs]
            for h in seq:
                src.append(N + h); dst.append(plat[(li, h)]); cost.append(BOARD_WAIT_S)
                src.append(plat[(li, h)]); dst.append(N + h); cost.append(0.0)
            for a, b in zip(seq[:-1], seq[1:]):
                d = np.hypot(metro.hubs[a]["x"] - metro.hubs[b]["x"], metro.hubs[a]["y"] - metro.hubs[b]["y"])
                t = d * RIDE_CIRCUITY / RIDE_KMH * 3600.0 + DWELL_S
                src += [plat[(li, a)], plat[(li, b)]]; dst += [plat[(li, b)], plat[(li, a)]]; cost += [t, t]
    base = base_g.tocoo()
    g = coo_matrix((np.concatenate((base.data, cost)),
                    (np.concatenate((base.row, src)).astype(int), np.concatenate((base.col, dst)).astype(int))),
                   shape=(total, total)).tocsr()
    return g, [N + h for h in active_hubs], N
