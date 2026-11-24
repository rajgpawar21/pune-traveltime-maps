import numpy as np
import pytest
from scipy.sparse import coo_matrix

from traveltime import metro, proximity, router
from traveltime.geo import Projection
from traveltime.osm_scan import area_m2, lifestyle_of, office_weight


def test_chain_order_follows_a_line_even_when_given_shuffled():
    pts = np.array([[x, 0.05 * x] for x in range(8)], float)
    perm = np.random.default_rng(0).permutation(8)
    order = metro.chain_order(pts[perm])
    xs = pts[perm][order][:, 0]
    assert list(xs) == sorted(xs) or list(xs) == sorted(xs, reverse=True)


def test_office_weights_use_footprint_and_skip_schools():
    assert office_weight({"office": "educational_institution"}) is None
    assert office_weight({"office": "it"}) == 30.0
    big = office_weight({"building": "office"}, area=12000.0, is_way=True)
    assert big == pytest.approx(1000.0)
    assert office_weight({"building": "commercial"}, area=3000.0, is_way=True) == pytest.approx(100.0)
    assert office_weight({"building": "yes"}, area=3000.0, is_way=True) is None


def test_lifestyle_tiers_malls_outweigh_cafes():
    assert lifestyle_of({"shop": "mall"})[1] > 20 * lifestyle_of({"amenity": "cafe"})[1]
    assert lifestyle_of({"amenity": "cinema"})[0] == "cinemas"
    assert lifestyle_of({"leisure": "park"}, area=200000.0)[1] == pytest.approx(6.0)
    assert lifestyle_of({"amenity": "bank"}) is None


def test_area_m2_square():
    ring = np.array([[73.0, 18.0], [73.001, 18.0], [73.001, 18.001], [73.0, 18.001], [73.0, 18.0]])
    assert area_m2(ring) == pytest.approx(111.32 * np.cos(np.radians(18.0005)) * 110.574, rel=0.005)


def test_sez_boost_preserves_mass(tmp_path):
    f = tmp_path / "sez.csv"
    f.write_text("Developer,Sector_group,Latitude_approx,Longitude_approx\nA,IT / ITES & software,18.5,73.8\n"
                 "B,Textile, apparel & footwear,18.5,73.8\n".replace("Textile, apparel", '"Textile, apparel'
                 ).replace("footwear,18.5", 'footwear",18.5'))
    proj = Projection(73.8, 18.5)
    g = np.array([(x, y) for y in np.arange(-10, 10.1, 1) for x in np.arange(-10, 10.1, 1)], float)
    out, used = proximity.sez_boost(f, proj, g, mass=1000.0, sigma_km=2.0)
    assert len(used) == 2 and out.sum() == pytest.approx(1000.0 * 1.0 + 1000.0 * 0.3, rel=1e-6)
    assert out[np.argmin(np.hypot(g[:, 0], g[:, 1]))] == out.max()


def test_composite_is_a_geometric_mean_and_zero_pillar_hurts():
    p = {"work": np.array([0.9, 0.9]), "lifestyle": np.array([0.9, 0.9]), "metro": np.array([0.9, 0.0])}
    c = proximity.composite(p, {"work": 1, "lifestyle": 1, "metro": 1})
    assert c[0] == pytest.approx(0.9) and c[1] < 0.35
    # a pillar with zero weight (or absent) is ignored, weights are re-normalised
    c2 = proximity.composite({"work": np.array([0.8]), "lifestyle": np.array([0.2])}, {"work": 1, "lifestyle": 1, "metro": 1})
    assert c2[0] == pytest.approx(np.sqrt(0.16))


def test_lifestyle_score_respects_category_sliders():
    cats = {"dining": np.array([1.0]), "malls": np.array([0.0])}
    assert proximity.lifestyle_score(cats, {"dining": 1, "malls": 0})[0] == 1.0
    assert proximity.lifestyle_score(cats, {"dining": 1, "malls": 1})[0] == 0.5


def _toy_metro():
    # two stations 10 km apart on a line; roads are a slow 3 km/h-equivalent chain (long edges)
    hubs = [{"name": "A", "x": 0.0, "y": 0.0, "lines": [0]}, {"name": "B", "x": 10.0, "y": 0.0, "lines": [0]}]
    ln = metro.Line("l", "Line", "#fff", "open", 2020, order=[0, 1])
    return metro.Metro([ln], hubs)


def test_metro_beats_a_slow_road_for_long_trips_and_only_after_opening():
    road_xy = np.array([[0.0, 0.0], [5.0, 0.0], [10.0, 0.0]])
    slow = 3600.0  # 1 h per 5 km hop
    g = coo_matrix(([slow, slow, slow, slow], ([0, 1, 1, 2], [1, 0, 2, 1])), shape=(3, 3)).tocsr()
    M = _toy_metro()
    snap, snapkm = np.array([0, 2]), np.zeros(2)
    drive = router.od_minutes(g, snap, snapkm)[0, 1]
    before, _, _ = metro.build_union(g, road_xy, M, 2019)
    after, _, _ = metro.build_union(g, road_xy, M, 2021)
    t_before = router.od_minutes(before, snap, snapkm)[0, 1]
    t_after = router.od_minutes(after, snap, snapkm)[0, 1]
    assert t_before == pytest.approx(drive)                    # line not open yet: nothing changes
    assert t_after < drive / 2                                 # 10 km at 32 km/h plus waits << 120 min
    assert t_after > 10 / metro.RIDE_KMH * 60                  # but never faster than the train itself


def test_transfer_costs_an_extra_wait():
    hubs = [{"name": n, "x": x, "y": 0.0, "lines": l} for n, x, l in
            (("A", 0.0, [0]), ("X", 5.0, [0, 1]), ("B", 10.0, [1]))]
    lines = [metro.Line("a", "A", "#fff", "open", 2020, order=[0, 1]), metro.Line("b", "B", "#fff", "open", 2020, order=[1, 2])]
    M = metro.Metro(lines, hubs)
    road_xy = np.array([[0.0, 0.0], [10.0, 0.0]])
    g = coo_matrix(([1e6, 1e6], ([0, 1], [1, 0])), shape=(2, 2)).tocsr()   # roads useless
    u, _, _ = metro.build_union(g, road_xy, M, 2021)
    t = router.od_minutes(u, np.array([0, 1]), np.zeros(2))[0, 1]
    ride = 2 * (5 * metro.RIDE_CIRCUITY / metro.RIDE_KMH * 3600 + metro.DWELL_S)
    waits = 2 * metro.BOARD_WAIT_S                              # board line a, then board line b
    access = 2 * (30.0)
    assert t * 60 == pytest.approx(ride + waits + access, rel=0.01)


def test_active_years_and_station_overrides():
    hubs = [{"name": n, "x": float(i), "y": 0.0, "lines": [0]} for i, n in enumerate("ABC")]
    ln = metro.Line("l", "L", "#fff", "open", 2020, order=[0, 1, 2], station_open={"C": 2025})
    M = metro.Metro([ln], hubs)
    assert M.active(2019) == {} and M.active(2021) == {0: {0, 1}} and M.active(2025) == {0: {0, 1, 2}}
