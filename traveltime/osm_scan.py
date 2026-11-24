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
            if not (hw in DRIVABLE or rw in RAIL or ww in ("river", "canal", "stream") or bld):
                continue
            c = _coords(o.nodes)
            if c is None or len(c) < 2:
                continue
            mid = c.mean(axis=0)
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
    key = f"{pbf.stem}-{pbf.stat().st_size}-{pbf.stat().st_mtime_ns}.pkl"
    cache = cache_dir / key
    if cache.exists():
        return pickle.loads(cache.read_bytes())
    scan = scan_pbf(pbf, bbox_margin)
    cache.write_bytes(pickle.dumps(scan))
    return scan
