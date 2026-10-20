"""
Unit tests for cycling_analysis/ine.py's pure-computation part
(name normalization + match_ine_to_gadm) - no network I/O
(fetch_ine_population hits INE directly and was validated manually against a
live pull, see ine.py's module docstring and CLAUDE.md's Known gaps).
"""

import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.ine import (
    _municipality_candidates,
    _normalize_province,
    _reorder_trailing_article,
    match_ine_to_gadm,
)


def test_reorder_trailing_article_moves_castilian_article_to_front():
    assert _reorder_trailing_article('Rioja, La') == 'La Rioja'
    assert _reorder_trailing_article('Yebenes, Los') == 'Los Yebenes'


def test_reorder_trailing_article_handles_balearic_and_elided_forms():
    assert _reorder_trailing_article('Balears, Illes') == 'Illes Balears'
    assert _reorder_trailing_article('Mercadal, Es') == 'Es Mercadal'
    assert _reorder_trailing_article("Atzubia, l'") == "l'Atzubia"


def test_reorder_trailing_article_leaves_names_without_a_trailing_article():
    assert _reorder_trailing_article('Barcelona') == 'Barcelona'
    assert _reorder_trailing_article('Sant Cugat del Valles') == 'Sant Cugat del Valles'


def test_normalize_province_applies_hardcoded_overrides():
    # These 7 provinces use a genuinely different word between INE and GADM,
    # not just an accent/word-order difference - see ine.py's docstring.
    assert _normalize_province('Alicante/Alacant') == 'alicante'
    assert _normalize_province('Valencia/València') == 'valencia'
    assert _normalize_province('Araba/Álava') == 'alava'
    assert _normalize_province('Bizkaia') == 'vizcaya'
    assert _normalize_province('Gipuzkoa') == 'guipuzcoa'
    assert _normalize_province('Balears, Illes') == 'baleares'


def test_normalize_province_plain_accent_case_needs_no_override():
    assert _normalize_province('Almería') == _normalize_province('almeria')


def test_municipality_candidates_tries_both_halves_of_a_bilingual_name():
    cands = _municipality_candidates('Xàbia/Jávea')
    assert 'xabia' in cands
    assert 'javea' in cands


def _muni_gdf(rows):
    # rows: list of (name_1, name_2, name_4)
    return gpd.GeoDataFrame(
        {
            'NAME_1': [r[0] for r in rows],
            'NAME_2': [r[1] for r in rows],
            'NAME_4': [r[2] for r in rows],
        },
        geometry=[Point(0, 0)] * len(rows),
        crs='EPSG:4326',
    )


def test_match_ine_to_gadm_matches_plain_and_bilingual_names():
    gdf = _muni_gdf([
        ('Cataluña', 'Barcelona', 'Sabadell'),
        ('ComunidadValenciana', 'Valencia', 'Xabia'),   # GADM keeps the Castilian half
        ('LaRioja', 'LaRioja', 'Logroño'),
    ])
    ine_df = pd.DataFrame([
        {'province_name': 'Barcelona', 'municipality_name': 'Sabadell', 'population': 208_346},
        {'province_name': 'Valencia/València', 'municipality_name': 'Xàbia/Jávea', 'population': 28_531},
        {'province_name': 'Rioja, La', 'municipality_name': 'Logroño', 'population': 151_120},
    ])

    result = match_ine_to_gadm(gdf, ine_df)

    assert result['population'].tolist() == [208_346, 28_531, 151_120]


def test_match_ine_to_gadm_leaves_unmatched_rows_as_nan_not_zero():
    gdf = _muni_gdf([('Cataluña', 'Barcelona', 'Sabadell'), ('Cataluña', 'Barcelona', 'NoSuchPlace')])
    ine_df = pd.DataFrame([
        {'province_name': 'Barcelona', 'municipality_name': 'Sabadell', 'population': 208_346},
    ])

    result = match_ine_to_gadm(gdf, ine_df)

    assert result['population'].iloc[0] == 208_346
    assert np.isnan(result['population'].iloc[1])


def test_match_ine_to_gadm_does_not_mutate_input():
    gdf = _muni_gdf([('Cataluña', 'Barcelona', 'Sabadell')])
    ine_df = pd.DataFrame([
        {'province_name': 'Barcelona', 'municipality_name': 'Sabadell', 'population': 208_346},
    ])

    match_ine_to_gadm(gdf, ine_df)

    assert 'population' not in gdf.columns
