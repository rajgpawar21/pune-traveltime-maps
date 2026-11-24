"""Local equirectangular projection (km) and the regular sampling grid."""
from __future__ import annotations

from dataclasses import dataclass

import numpy as np

KM_PER_DEG_LAT = 110.574
KM_PER_DEG_LON_EQ = 111.320


@dataclass(frozen=True)
class Projection:
    lon0: float
    lat0: float

    def fwd(self, lon, lat):
        lon, lat = np.asarray(lon, float), np.asarray(lat, float)
        x = (lon - self.lon0) * KM_PER_DEG_LON_EQ * np.cos(np.radians(self.lat0))
        y = (lat - self.lat0) * KM_PER_DEG_LAT
        return x, y

    def inv(self, x, y):
        x, y = np.asarray(x, float), np.asarray(y, float)
        lon = self.lon0 + x / (KM_PER_DEG_LON_EQ * np.cos(np.radians(self.lat0)))
        lat = self.lat0 + y / KM_PER_DEG_LAT
        return lon, lat


def projection_for(bbox):
    west, south, east, north = bbox
    return Projection((west + east) / 2, (south + north) / 2)


def make_grid(bbox, spacing_km, proj: Projection):
    """Cell-centre grid covering bbox. Returns xy (n,2) km, lonlat (n,2), nx, ny."""
    west, south, east, north = bbox
    x0, y0 = proj.fwd(west, south)
    x1, y1 = proj.fwd(east, north)
    nx = max(2, int(round((x1 - x0) / spacing_km)))
    ny = max(2, int(round((y1 - y0) / spacing_km)))
    xs = x0 + (np.arange(nx) + 0.5) * (x1 - x0) / nx
    ys = y0 + (np.arange(ny) + 0.5) * (y1 - y0) / ny
    gx, gy = np.meshgrid(xs, ys)  # row-major: south -> north, west -> east
    xy = np.column_stack((gx.ravel(), gy.ravel()))
    lon, lat = proj.inv(xy[:, 0], xy[:, 1])
    return xy, np.column_stack((lon, lat)), nx, ny, ((x1 - x0) / nx, (y1 - y0) / ny)
