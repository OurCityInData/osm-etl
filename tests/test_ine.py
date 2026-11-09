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
import pytest
from shapely.geometry import Point

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.ine import (
    _municipality_candidates,
    _normalize_province,
    _reorder_trailing_article,
    assign_ine_codes,
    match_ine_to_gadm,
    municipality_group_key,
    reconcile_population,
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


# --- one INE municipality, one population (split GADM municipalities) --------------

def _ine(rows):
    # rows: (code, province, name, population)
    return pd.DataFrame(rows, columns=['ine_code', 'province_name', 'municipality_name', 'population'])


def _muni_gdf_with_area(rows):
    # rows: (name_1, name_2, name_4, area_km2)
    gdf = _muni_gdf([r[:3] for r in rows])
    gdf['area_km2'] = [r[3] for r in rows]
    return gdf


def test_population_goes_to_the_largest_piece_only():
    gdf = _muni_gdf_with_area([
        ('ComunidadValenciana', 'Valencia', 'Valencia', 2.9),     # small piece listed first on purpose
        ('ComunidadValenciana', 'Valencia', 'Valencia', 133.2),   # main body
    ])
    ine = _ine([('46250', 'Valencia/València', 'València', 840_792)])

    out = match_ine_to_gadm(gdf, ine)

    assert out['ine_code'].tolist() == ['46250', '46250']
    assert out['ine_main'].tolist() == [False, True]
    assert np.isnan(out['population'].iloc[0])
    assert out['population'].iloc[1] == 840_792
    assert out['population'].sum() == 840_792          # never counted twice


def test_unmatched_rows_each_stay_their_own_main():
    gdf = _muni_gdf_with_area([('X', 'Burgos', 'ComunidaddeAlgo', 4.4), ('X', 'Burgos', 'ComunidaddeAlgo', 0.2)])
    out = match_ine_to_gadm(gdf, _ine([('09999', 'Burgos', 'Other', 5)]))

    assert out['ine_code'].isna().all()
    assert out['ine_main'].tolist() == [True, True]


def test_aliases_bridge_palma_and_els_alamus():
    gdf = _muni_gdf_with_area([
        ('IslasBaleares', 'Baleares', 'PalmadeMallorca', 197.6),
        ('IslasBaleares', 'Baleares', 'PalmadeMallorca', 14.1),   # Cabrera
        ('Cataluña', 'Lleida', 'ElAlamús', 12.6),
    ])
    ine = _ine([('07040', 'Balears, Illes', 'Palma', 434_786), ('25012', 'Lleida', 'Alamús, Els', 800)])

    out = match_ine_to_gadm(gdf, ine)

    assert out['ine_code'].tolist() == ['07040', '07040', '25012']
    assert out['population'].fillna(0).tolist() == [434_786, 0, 800]


def test_ambiguous_name_in_a_province_is_left_unmatched_not_guessed():
    # two INE municipalities in one province share the candidate name 'villa'
    ine = _ine([('01001', 'Burgos', 'Villa/Alfa', 10), ('01002', 'Burgos', 'Beta/Villa', 20)])
    gdf = _muni_gdf_with_area([('X', 'Burgos', 'Villa', 1.0)])

    assert assign_ine_codes(gdf, ine).isna().all()


def test_municipality_group_key_groups_pieces_by_code_and_falls_back_to_name():
    gdf = _muni_gdf_with_area([('X', 'Valencia', 'Valencia', 133.2), ('X', 'Valencia', 'Valencia', 2.9),
                               ('X', 'Burgos', 'Comunidad de X', 1.0), ('X', 'Burgos', 'Comunidad de X', 0.5)])
    gdf['ine_code'] = ['46250', '46250', np.nan, np.nan]

    keys = municipality_group_key(gdf)

    assert keys.iloc[0] == keys.iloc[1] and keys.iloc[0].startswith('ine:')
    assert keys.iloc[2] == keys.iloc[3] and keys.iloc[2].startswith('name:')
    assert keys.iloc[0] != keys.iloc[2]


def test_reconcile_population_passes_and_reports_unplaced_people():
    gdf = _muni_gdf_with_area([('X', 'Burgos', 'Burgos', 10.0)])
    ine = _ine([('09059', 'Burgos', 'Burgos', 177_000), ('09999', 'Burgos', 'Nowhere', 500)])

    report = reconcile_population(match_ine_to_gadm(gdf, ine), ine)

    assert report['unplaced_count'] == 1 and report['unplaced_population'] == 500


def test_reconcile_population_raises_on_double_assignment():
    ine = _ine([('46250', 'Valencia', 'Valencia', 840_792)])
    bad = _muni_gdf_with_area([('X', 'Valencia', 'Valencia', 133.2), ('X', 'Valencia', 'Valencia', 2.9)])
    bad['ine_code'] = ['46250', '46250']
    bad['population'] = [840_792.0, 840_792.0]            # the old bug: full population on both pieces

    with pytest.raises(ValueError, match='more than one'):
        reconcile_population(bad, ine)
