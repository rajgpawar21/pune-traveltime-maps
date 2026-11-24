import numpy as np
import pytest

from traveltime import embed, metrics, router
from traveltime.geo import Projection
from traveltime.osm_scan import Scan


def test_smacof_recovers_planar_geometry():
    rng = np.random.default_rng(0)
    P = rng.uniform(0, 10, (40, 2))
    D = embed.pairwise(P)
    X, _ = embed.weighted_smacof(D, P + rng.normal(0, 0.8, P.shape), alpha=1.0, max_iter=800)
    assert np.abs(embed.pairwise(X) - D).max() < 0.05


def test_scale_is_not_renormalised():
    """Doubling every travel time must double the map; no hidden min-max scaling."""
    rng = np.random.default_rng(1)
    P = rng.uniform(0, 10, (30, 2))
    D = embed.pairwise(P)
    X1, _ = embed.weighted_smacof(D, P)
    X2, _ = embed.weighted_smacof(2 * D, 2 * P)
    assert embed.pairwise(X2).mean() == pytest.approx(2 * embed.pairwise(X1).mean(), rel=0.02)


def test_symmetrise_uses_one_direction_when_other_missing():
    m = np.array([[0, 5.0, np.nan], [7.0, 0, 2.0], [4.0, 2.0, 0]])
    s = embed.symmetrise(m)
    assert s[0, 1] == 6.0 and s[0, 2] == 4.0 and np.allclose(s, s.T)


def test_symmetrise_rejects_unreachable_pairs():
    with pytest.raises(ValueError):
        embed.symmetrise(np.array([[0, np.nan], [np.nan, 0]]))


def test_field_is_fold_free_and_follows_a_smooth_warp():
    g = np.linspace(-5, 5, 11)
    G = np.array([(x, y) for y in g for x in g])
    r = np.hypot(G[:, 0], G[:, 1])[:, None]
    X = G * (1 - 0.35 * np.exp(-r ** 2 / 18))            # compress the centre
    lat = embed.Lattice((-8, -8, 8, 8), 0.25)
    f = embed.fit_field(G, X, lat, (-5, -5, 5, 5), (-5.6, -5.6, 5.6, 5.6))
    assert f["min_j"] >= 0.2
    got = embed.apply_field(lat, f["displacement"], G)
    assert np.abs(got - X).max() < 0.5


def test_field_never_returns_folds_even_for_impossible_targets():
    g = np.linspace(-5, 5, 9)
    G = np.array([(x, y) for y in g for x in g])
    rng = np.random.default_rng(2)
    X = G + rng.normal(0, 1.5, G.shape)                    # wildly inconsistent target
    lat = embed.Lattice((-8, -8, 8, 8), 0.25)
    f = embed.fit_field(G, X, lat, (-5, -5, 5, 5), (-5.6, -5.6, 5.6, 5.6))
    assert f["min_j"] >= 0.2 - 1e-6 and 0 < f["alpha"] <= 1


def test_fidelity_reports_both_map_and_geography():
    P = np.random.default_rng(3).uniform(0, 20, (60, 2))
    out = embed.fidelity(P, embed.pairwise(P), P)
    assert out["spearman_map"] == pytest.approx(1.0) and out["bands_km"]
    assert all(b["err_map"] < 1e-9 for b in out["bands_km"])


def _line_scan(oneway=0, cls="primary"):
    # three nodes ~1.1 km apart along a meridian, joined by two ways sharing node 2
    ll1 = np.array([[73.0, 18.00], [73.0, 18.01]]); ll2 = np.array([[73.0, 18.01], [73.0, 18.02]])
    s = Scan()
    s.roads = [(cls, oneway, np.array([1, 2]), ll1), (cls, oneway, np.array([2, 3]), ll2)]
    return s


def test_router_two_way_is_symmetric():
    proj = Projection(73.0, 18.01)
    pts = np.column_stack((np.zeros(3), (np.array([18.0, 18.01, 18.02]) - 18.01) * 110.574))
    both, _ = router.builtin_matrix(_line_scan(0), proj, pts)
    assert np.allclose(both, both.T) and both[0, 2] > both[0, 1] > 0


def test_router_respects_one_way_streets():
    """A one-way triangle: going with the flow is one hop, against it is two."""
    a, b, c = [73.00, 18.00], [73.01, 18.00], [73.005, 18.009]
    s = Scan()
    for i, (p, q) in enumerate(((a, b), (b, c), (c, a))):
        s.roads.append(("primary", 1, np.array([i + 1, (i + 1) % 3 + 1]), np.array([p, q])))
    proj = Projection(73.005, 18.003)
    xy = np.column_stack(proj.fwd([a[0], b[0], c[0]], [a[1], b[1], c[1]]))
    t, _ = router.builtin_matrix(s, proj, xy)
    assert t[0, 1] < t[1, 0] * 0.7           # a->b is direct; b->a must go round via c


def test_speed_scale_speeds_up_that_class_only():
    proj = Projection(73.0, 18.01)
    pts = np.column_stack((np.zeros(2), (np.array([18.0, 18.02]) - 18.01) * 110.574))
    base, _ = router.builtin_matrix(_line_scan(0, "primary"), proj, pts)
    fast, _ = router.builtin_matrix(_line_scan(0, "primary"), proj, pts, {"primary": 2.0})
    other, _ = router.builtin_matrix(_line_scan(0, "primary"), proj, pts, {"residential": 2.0})
    assert fast[0, 1] < base[0, 1] * 0.7 and other[0, 1] == pytest.approx(base[0, 1])


def test_accessibility_rewards_nearby_activity_and_excludes_self():
    t = np.array([[0, 5, 60], [5, 0, 60], [60, 60, 0]], float)
    raw = np.array([100.0, 100.0, 100.0])
    acc = metrics.gravity_accessibility(t, raw, half_life_min=10)
    assert acc[0] > acc[2] and acc[0] == pytest.approx(acc[1])
    isolated = metrics.gravity_accessibility(np.array([[0, 1e6], [1e6, 0]]), np.array([1e4, 1e4]))
    assert isolated.max() < 0.01                           # own activity does not count


def test_percentile_scores_are_comparable_across_snapshots():
    base = np.arange(100.0)
    assert metrics.percentile_vs(base, np.array([50.0, 200.0]))[1] == 1.0   # growth can exceed baseline max


def test_clusters_are_contiguous_blocks():
    score = np.zeros((6, 6)); score[1:4, 1:4] = 0.9; score[5, 5] = 0.9      # 3x3 block + stray cell
    lab = metrics.clusters(score.ravel(), np.ones(36, bool), (6, 6), 0.75, 3).reshape(6, 6)
    assert lab[2, 2] == 1 and lab[5, 5] == 0 and (lab > 0).sum() == 9
