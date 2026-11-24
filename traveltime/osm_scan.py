"""Single pass over an OSM PBF: roads, rail, water, green, places, activity.

One scan feeds three consumers: the router (drivable graph), the viewer
(vector layers) and the activity metrics (POI / building density).
"""
from __future__ import annotations

import pickle
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import osmium

DRIVABLE = {
    "motorway", "motorway_link", "trunk", "trunk_link", "primary", "primary_link",
    "secondary", "secondary_link", "tertiary", "tertiary_link", "unclassified",
    "residential", "living_street", "service",
}
RAIL = {"rail", "light_rail", "subway", "monorail"}
GREEN_LEISURE = {"park", "garden", "nature_reserve", "recreation_ground", "golf_course"}
GREEN_LANDUSE = {"forest", "grass", "meadow", "village_green", "recreation_ground", "cemetery"}
GREEN_NATURAL = {"wood", "scrub", "grassland"}
PLACE_RANK = {"city": 0, "town": 0, "suburb": 1, "borough": 1, "quarter": 2,
              "neighbourhood": 2, "village": 2, "locality": 3}
POI_KEYS = ("amenity", "shop", "office", "tourism", "healthcare")
POI_SKIP = {"bench", "waste_basket", "parking_entrance", "bicycle_parking", "drinking_water",
            "shelter", "toilets", "recycling", "post_box", "street_lamp", "atm"}


@dataclass
class Scan:
    roads: list = field(default_factory=list)     # (cls, oneway, node_ids, lonlat(n,2))
    rail: list = field(default_factory=list)      # (kind, lonlat)
    waterways: list = field(default_factory=list)  # lonlat
    water: list = field(default_factory=list)     # ring lonlat
    green: list = field(default_factory=list)     # ring lonlat
    places: list = field(default_factory=list)    # (name, rank, lon, lat)
    stations: list = field(default_factory=list)  # (name, is_metro, lon, lat)
    poi: list = field(default_factory=list)       # (lon, lat)
    buildings: list = field(default_factory=list)  # (lon, lat)
    offices: list = field(default_factory=list)    # (lon, lat, headcount proxy)
    lifestyle: list = field(default_factory=list)  # (category, lon, lat, weight)
    metro_ways: list = field(default_factory=list)  # (name, status, lonlat)
    metro_nodes: list = field(default_factory=list)  # (name, lon, lat)


LIFESTYLE = {  # (tag key, value) -> (category, weight)
    ("amenity", "restaurant"): ("dining", 1.0), ("amenity", "fast_food"): ("dining", 0.6),
    ("amenity", "cafe"): ("dining", 1.0), ("amenity", "food_court"): ("dining", 3.0),
    ("amenity", "ice_cream"): ("dining", 0.4), ("shop", "bakery"): ("dining", 0.3),
    ("amenity", "bar"): ("nightlife", 1.2), ("amenity", "pub"): ("nightlife", 1.2),
    ("amenity", "nightclub"): ("nightlife", 2.5),
    ("shop", "mall"): ("malls", 40.0), ("shop", "department_store"): ("malls", 12.0),
    ("amenity", "cinema"): ("cinemas", 6.0), ("amenity", "theatre"): ("cinemas", 2.0),
    ("leisure", "park"): ("parks", 1.0), ("leisure", "garden"): ("parks", 0.5),
}
LIFESTYLE_CATEGORIES = ["dining", "nightlife", "malls", "cinemas", "parks"]
OFFICE_SKIP = {"educational_institution", "ngo", "religion", "association", "political"}
METRO_RAIL = {"subway", "light_rail", "monorail"}


def area_m2(c):
    """Shoelace area of a lon/lat ring in square metres (local scale)."""
    c = np.asarray(c, float)
    lat0 = np.radians(c[:, 1].mean())
    x = (c[:, 0] - c[0, 0]) * 111320.0 * np.cos(lat0)
    y = (c[:, 1] - c[0, 1]) * 110574.0
    return abs(0.5 * float(np.sum(x * np.roll(y, -1) - np.roll(x, -1) * y)))


def office_weight(tags, area=0.0, is_way=False):
    """Headcount proxy. Offices: ~12 m2 per head; commercial buildings: ~30 m2 (mixed use)."""
    o = tags.get("office")
    if o in OFFICE_SKIP:
        return None
    if o or tags.get("building") == "office":
        base = 30.0 if o == "it" else 15.0
        return max(base, min(5000.0, area / 12.0)) if is_way else base
    if is_way and tags.get("building") == "commercial" and area > 0:
        return min(3000.0, area / 30.0)
    return None


def lifestyle_of(tags, area=0.0):
    for key in ("amenity", "shop", "leisure"):
        hit = LIFESTYLE.get((key, tags.get(key)))
        if hit:
            cat, w = hit
            if cat == "parks" and tags.get("leisure") == "park":
                w = 1.0 + min(5.0, area / 20000.0)
            return cat, w
    return None


def _coords(nodes):
    try:
        return np.array([(n.lon, n.lat) for n in nodes], dtype=float)
    except osmium.InvalidLocationError:
        return None


def _oneway(tags, cls):
    v = tags.get("oneway")
    if v in ("yes", "true", "1"):
        return 1
    if v == "-1":
        return -1
    if v in ("no", "false", "0"):
        return 0
    if tags.get("junction") in ("roundabout", "circular") or cls.startswith("motorway"):
        return 1
    return 0


def scan_pbf(path, bbox_margin=None) -> Scan:
    """bbox_margin = (west, south, east, north) lon/lat filter for what is kept."""
    scan = Scan()

    def inside(lon, lat):
        if bbox_margin is None:
            return True
        w, s, e, n = bbox_margin
        return w <= lon <= e and s <= lat <= n

    fp = osmium.FileProcessor(str(path)).with_locations().with_areas()
    for o in fp:
        tags = o.tags
        if o.is_node():
            if not tags or not o.location.valid():
                continue
            lon, lat = o.location.lon, o.location.lat
            if not inside(lon, lat):
                continue
            ow = office_weight(tags)
            if ow:
                scan.offices.append((lon, lat, ow))
            ls = lifestyle_of(tags)
            if ls:
                scan.lifestyle.append((ls[0], lon, lat, ls[1]))
            if tags.get("railway") in ("station", "halt") and (
                    tags.get("station") in ("subway", "light_rail") or
                    "metro" in (tags.get("network", "") + tags.get("operator", "")).lower()):
                scan.metro_nodes.append((tags.get("name:en") or tags.get("name") or "Station", lon, lat))
            place = tags.get("place")
            if place in PLACE_RANK and tags.get("name"):
                scan.places.append((tags.get("name:en") or tags["name"], PLACE_RANK[place], lon, lat))
            if tags.get("railway") in ("station", "halt") and tags.get("name"):
                metro = tags.get("station") == "subway" or tags.get("subway") == "yes" \
                    or tags.get("network", "").lower().find("metro") >= 0
                scan.stations.append((tags.get("name:en") or tags["name"], bool(metro), lon, lat))
            for key in POI_KEYS:
                val = tags.get(key)
                if val and val not in POI_SKIP:
                    scan.poi.append((lon, lat))
                    break
        elif o.is_way():
            if not tags:
                continue
            hw = tags.get("highway")
            rw = tags.get("railway")
            ww = tags.get("waterway")
            bld = tags.get("building")
            is_metro = rw in METRO_RAIL or (rw == "construction" and tags.get("construction") in METRO_RAIL)
            prox = bool(tags.get("office") or tags.get("amenity") or tags.get("shop") or tags.get("leisure"))
            if not (hw in DRIVABLE or rw in RAIL or is_metro or ww in ("river", "canal", "stream") or bld or prox):
                continue
            c = _coords(o.nodes)
            if c is None or len(c) < 2:
                continue
            mid = c.mean(axis=0)
            if is_metro:
                nm = tags.get("name") or tags.get("name:en") or tags.get("ref") or ""
                scan.metro_ways.append((nm, "open" if rw in METRO_RAIL else "construction", c))
            if (bld or prox) and len(c) >= 4 and inside(*mid):
                a = area_m2(c)
                ow = office_weight(tags, a, True)
                if ow:
                    scan.offices.append((mid[0], mid[1], ow))
                ls = lifestyle_of(tags, a)
                if ls:
                    scan.lifestyle.append((ls[0], mid[0], mid[1], ls[1]))
            if not inside(*mid) and not (inside(*c[0]) or inside(*c[-1])):
                continue
            if hw in DRIVABLE:
                if tags.get("area") == "yes" or tags.get("access") in ("private", "no"):
                    if hw == "service":
                        continue
                ids = np.array([n.ref for n in o.nodes], dtype=np.int64)
                scan.roads.append((hw, _oneway(tags, hw), ids, c))
            elif rw in RAIL:
                scan.rail.append((rw, c))
            elif ww in ("river", "canal") and tags.get("tunnel") != "yes":
                scan.waterways.append(c)
            elif bld and inside(*mid):
                scan.buildings.append((mid[0], mid[1]))
        elif o.is_area():
            if not tags:
                continue
            water = tags.get("natural") == "water" or tags.get("landuse") in ("reservoir", "basin") \
                or tags.get("waterway") == "riverbank"
            green = tags.get("leisure") in GREEN_LEISURE or tags.get("landuse") in GREEN_LANDUSE \
                or tags.get("natural") in GREEN_NATURAL
            if not (water or green):
                continue
            for ring in o.outer_rings():
                c = _coords(ring)
                if c is None or len(c) < 4:
                    continue
                if not inside(*c.mean(axis=0)):
                    continue
                (scan.water if water else scan.green).append(c)
    return scan


def cached_scan(pbf, cache_dir, bbox_margin=None) -> Scan:
    pbf, cache_dir = Path(pbf), Path(cache_dir)
    cache_dir.mkdir(parents=True, exist_ok=True)
    key = f"v2-{pbf.stem}-{pbf.stat().st_size}-{pbf.stat().st_mtime_ns}.pkl"
    cache = cache_dir / key
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    scan = scan_pbf(pbf, bbox_margin)
    cache.write_bytes(pickle.dumps(scan))
    return scan
