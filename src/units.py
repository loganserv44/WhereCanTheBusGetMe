#!/usr/bin/env python3
"""Metric -> US conversions for everything a reader sees.

The pipeline computes in metric, because that is what the routing engine and the map
projection use: r5py takes km/h, UTM is in meters. Readers in Lincoln think in miles.
So every number that reaches a map, caption, table or the published methodology goes
through this module, and there is exactly one place a conversion could be wrong.

Nothing here changes a stored result. config.yml and the other scripts stay metric.
"""

from __future__ import annotations

KM2_TO_SQ_MI = 0.3861021585424458
KM_H_TO_MPH = 0.6213711922373339
M_TO_FT = 3.2808398950131235
M_TO_MI = 0.0006213711922373339
MI_TO_M = 1609.344


def km2_to_sq_mi(km2: float) -> float:
    return km2 * KM2_TO_SQ_MI


def kmh_to_mph(kmh: float) -> float:
    return kmh * KM_H_TO_MPH


def m_to_ft(meters: float) -> float:
    return meters * M_TO_FT


def m_to_mi(meters: float) -> float:
    return meters * M_TO_MI


def fmt_sq_mi(km2: float) -> str:
    """Area for a reader: '43 sq mi', or one decimal when it is small enough to matter."""
    sq_mi = km2_to_sq_mi(km2)
    if sq_mi == 0:
        return "0 sq mi"
    return f"{sq_mi:.1f} sq mi" if sq_mi < 10 else f"{sq_mi:.0f} sq mi"


def fmt_mph(kmh: float) -> str:
    return f"{kmh_to_mph(kmh):.1f} mph"


def fmt_walk_distance(meters: float) -> str:
    """Walking distance: feet when short, else miles. 780 m -> 'about half a mile'."""
    miles = m_to_mi(meters)
    if miles < 0.2:
        return f"about {round(m_to_ft(meters) / 50) * 50:,.0f} ft"
    for fraction, words in ((0.25, "about a quarter mile"), (0.5, "about half a mile"),
                            (0.75, "about three quarters of a mile"), (1.0, "about a mile")):
        if abs(miles - fraction) < 0.08:
            return words
    return f"about {miles:.1f} miles"


def fmt_change(value_km2: float, baseline_km2: float) -> str | None:
    """'down 77%' against a baseline scenario. None when there is nothing to compare."""
    if baseline_km2 <= 0 or value_km2 == baseline_km2:
        return None
    pct = (value_km2 - baseline_km2) / baseline_km2 * 100
    if abs(pct) < 1:
        return "about the same"
    return f"{'down' if pct < 0 else 'up'} {abs(pct):.0f}%"
