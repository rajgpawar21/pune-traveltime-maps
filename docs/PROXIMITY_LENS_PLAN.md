# Proximity lens: plan (agreed 2026-11-24)

A second top-level toggle next to the existing *Activity × connectivity* analysis, which stays
unchanged. Same grid, same warp field, same time slider. Only the scoring layer changes.

## Decisions
| Topic | Decision |
|---|---|
| Audience | Persona presets: **buyer who lives there**, **rental-yield investor**, **commercial / retail**. Each sets default pillar weights. |
| Offices | OSM `office=*`, office/commercial buildings and commercial land use, **area-weighted** (footprint as headcount proxy), plus a **soft boost** from `india-sez-atlas` (277 points, locality-level accuracy, so each is spread as a ~2 km Gaussian, scaled by sector group, IT/ITES highest). |
| Lifestyle | Tiered weights (malls and large venues > single cafes) with **category sliders**: dining, malls, nightlife, parks, cinemas. |
| Metro | **Door-to-door multimodal** times and **planned lines** on the time slider. Timeline from an **editable curated YAML** (`config/metro/<city>.yaml`), flagged unverified until you correct it. Station geometry comes from OSM. |
| Score | **Live weight sliders** with a **weighted geometric mean** (a cell cannot score high with zero in one pillar). |
| Map shape | Selector under Stretch: **drive** (default) or **drive + metro**. |

## Pillars
1. **Work**: `Σ office_weight_j · 2^(−t_ij / half_life)`.
2. **Lifestyle**: the same gravity form over weighted lifestyle places.
3. **Metro**: time to nearest station, stations within 15 min, and the **metro premium**:
   jobs reachable in 30 min with metro minus without it.

Percentiles are taken against the first snapshot so growth can exceed the baseline (as in the current score).

## Build order
1. **Data**: extend `osm_scan` for offices (with footprint area), lifestyle categories and stations/lines;
   SEZ loader with sector scaling; metro YAML schema and loader. Tests for each.
2. **Router**: metro edges in the graph (access, ride ~32 km/h, 20 s dwell, transfer penalty, egress);
   per-snapshot active-line filter by opening year. Tests: transfers, one-direction lines, no-metro city.
3. **Scores**: three pillars, composite, persona presets, clusters, ranked CSV (`outputs/<city>_proximity.csv`).
4. **Viewer**: lens switch, pillar layers (Work blue, Lifestyle magenta, Metro teal), sliders, presets,
   map-shape selector, metro lines/stations drawn, legend and "how to read" text. Change layer keeps the
   night-time-lights R/G/B composite so a metro opening shows as a new corridor.
5. **Pune end to end**, then other cities. Bengaluru waits for the laptop-side PBF (branch `feature/bengaluru`).

## Validation and risks
- OSM office/POI completeness differs by city. Report counts per city and flag thin ones.
- SEZ points are approximate. Check that known IT corridors score high.
- Back-test against rent/price data: does the score at t₀ predict appreciation to t₁? Until then, hypotheses only.
- Cities without metro: Metro pillar switches off and weights re-normalise.
- Pune metro is partly open, so the curated YAML needs your check on opening years.
