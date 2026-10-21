"""
Unit tests for country.clip_roads_to_polygon() - no network, synthetic geometry.
"""

import os
import sys

import geopandas as gpd
from shapely.geometry import LineString, Polygon, box

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.country import clip_roads_to_polygon


def _roads(*lines):
    return gpd.GeoDataFrame(geometry=list(lines), crs='EPSG:25830')


def test_clip_keeps_inside_drops_outside_and_cuts_crossing():
    poly = box(0, 0, 10, 10)
    roads = _roads(
        LineString([(1, 5), (9, 5)]),      # inside   -> 8 kept whole
        LineString([(20, 5), (30, 5)]),    # outside  -> dropped
        LineString([(5, 5), (15, 5)]),     # crossing -> only the 5 inside kept
        LineString([(10, 0), (20, 0)]),    # touches the border at one point -> dropped
    )

    out = clip_roads_to_polygon(roads, poly)

    assert len(out) == 2
    assert out.geometry.length.sum() == 13.0


def test_clip_does_not_change_attributes_of_kept_roads():
    poly = box(0, 0, 10, 10)
    roads = _roads(LineString([(5, 5), (15, 5)]))
    roads['highway'] = 'cycleway'

    out = clip_roads_to_polygon(roads, poly)

    assert out['highway'].tolist() == ['cycleway']


def test_clip_returns_empty_when_nothing_intersects():
    out = clip_roads_to_polygon(_roads(LineString([(20, 5), (30, 5)])), box(0, 0, 10, 10))

    assert len(out) == 0


def test_clip_handles_invalid_polygon_without_crashing():
    bowtie = Polygon([(0, 0), (10, 10), (10, 0), (0, 10)])   # self-intersecting
    roads = _roads(LineString([(1, 5), (9, 5)]))

    out = clip_roads_to_polygon(roads, bowtie)

    assert len(out) >= 0


def test_clip_does_not_mutate_input():
    poly = box(0, 0, 10, 10)
    roads = _roads(LineString([(5, 5), (15, 5)]))

    clip_roads_to_polygon(roads, poly)

    assert roads.geometry.iloc[0].length == 10.0
