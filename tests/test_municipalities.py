"""
Unit tests for cycling_analysis/municipalities.py's merge_municipality_pieces()
- synthetic frames, no network.
"""

import os
import sys

import geopandas as gpd
import numpy as np
import pandas as pd
from shapely.geometry import Point

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.area_classification import CATEGORIES
from cycling_analysis.bike_amenities import AMENITY_METRIC_COLUMNS
from cycling_analysis.municipalities import apply_ine_names, merge_municipality_pieces
from cycling_analysis.pollution import POLLUTION_COLUMNS


def _summary(rows):
    """rows: (gid, name, area, population, greenway_km, shops, pm10, stations)"""
    recs = []
    for gid, name, area, pop, green, shops, pm10, stations in rows:
        rec = {'Region': 'R', 'Municipality': name, 'GADM ID': gid,
               'Municipality area (km²)': area, 'Population': pop,
               'Population density (per km²)': round(pop / area, 2) if pop == pop else np.nan}
        rec.update({c: 0.0 for c in CATEGORIES})
        rec.update({c: 0 for c in AMENITY_METRIC_COLUMNS})
        rec.update({c: np.nan for c in POLLUTION_COLUMNS})
        rec['Greenway'] = green
        rec['Mixed traffic road'] = green * 2
        rec['Bike shops'] = shops
        rec['PM10 median annual (µg/m³)'] = pm10
        rec['Station count'] = stations
        recs.append(rec)
    return pd.DataFrame(recs)


def _munis(rows):
    """rows: (gid, province, name, ine_code)"""
    return gpd.GeoDataFrame(
        {'GID_4': [r[0] for r in rows], 'NAME_2': [r[1] for r in rows], 'NAME_4': [r[2] for r in rows],
         'ine_code': [r[3] for r in rows]},
        geometry=[Point(0, 0)] * len(rows), crs='EPSG:4326')


def _two_piece_fixture():
    summary = _summary([
        ('G_SMALL', 'Valencia', 2.9, np.nan, 10.0, 1, 99.0, 1),     # extra piece (listed first on purpose)
        ('G_MAIN', 'Valencia', 133.2, 840_792.0, 100.0, 4, 20.0, 2),
        ('G_OTHER', 'Sagunto', 60.0, 70_000.0, 30.0, 2, 15.0, 0),
    ])
    munis = _munis([('G_SMALL', 'Valencia', 'Valencia', '46250'), ('G_MAIN', 'Valencia', 'Valencia', '46250'),
                    ('G_OTHER', 'Valencia', 'Sagunto', '46220')])
    return summary, munis


def test_pieces_merge_into_one_row_per_municipality():
    summary, munis = _two_piece_fixture()

    out = merge_municipality_pieces(summary, munis, gid_col='GID_4')

    assert len(out) == 2
    assert sorted(out['Municipality']) == ['Sagunto', 'Valencia']


def test_additive_columns_are_summed_across_pieces():
    summary, munis = _two_piece_fixture()

    v = merge_municipality_pieces(summary, munis, gid_col='GID_4').set_index('Municipality').loc['Valencia']

    assert v['Greenway'] == 110.0 and v['Mixed traffic road'] == 220.0
    assert v['Bike shops'] == 5
    assert abs(v['Municipality area (km²)'] - 136.1) < 1e-9
    assert v['Station count'] == 3


def test_population_is_not_summed_and_density_is_recomputed():
    summary, munis = _two_piece_fixture()

    v = merge_municipality_pieces(summary, munis, gid_col='GID_4').set_index('Municipality').loc['Valencia']

    assert v['Population'] == 840_792.0                               # the one non-NaN value, not doubled
    assert v['Population density (per km²)'] == round(840_792 / 136.1, 2)


def test_non_additive_columns_come_from_the_largest_piece():
    summary, munis = _two_piece_fixture()

    v = merge_municipality_pieces(summary, munis, gid_col='GID_4').set_index('Municipality').loc['Valencia']

    assert v['GADM ID'] == 'G_MAIN'
    assert v['PM10 median annual (µg/m³)'] == 20.0                    # main piece, not the small one's 99


def test_bookkeeping_columns_and_passthrough_of_unsplit_rows():
    summary, munis = _two_piece_fixture()

    out = merge_municipality_pieces(summary, munis, gid_col='GID_4').set_index('Municipality')

    assert out.loc['Valencia', 'Parts'] == 2
    assert out.loc['Valencia', 'GADM IDs'] == 'G_MAIN;G_SMALL'        # largest first
    assert out.loc['Valencia', 'INE code'] == '46250'
    assert out.loc['Sagunto', 'Parts'] == 1 and out.loc['Sagunto', 'GADM IDs'] == 'G_OTHER'
    assert out.loc['Sagunto', 'Greenway'] == 30.0 and out.loc['Sagunto', 'Population'] == 70_000.0


def test_unmatched_same_name_pieces_still_merge_by_name():
    summary = _summary([('A', 'Comunidad de X', 4.4, np.nan, 5.0, 0, np.nan, 0),
                        ('B', 'Comunidad de X', 0.2, np.nan, 1.0, 0, np.nan, 0)])
    munis = _munis([('A', 'Burgos', 'Comunidad de X', np.nan), ('B', 'Burgos', 'Comunidad de X', np.nan)])

    out = merge_municipality_pieces(summary, munis, gid_col='GID_4')

    assert len(out) == 1 and out['Greenway'].iloc[0] == 6.0 and np.isnan(out['Population'].iloc[0])


def test_total_km_is_preserved_by_merging():
    summary, munis = _two_piece_fixture()

    out = merge_municipality_pieces(summary, munis, gid_col='GID_4')

    for col in ['Greenway', 'Mixed traffic road', 'Bike shops', 'Municipality area (km²)']:
        assert abs(out[col].sum() - summary[col].sum()) < 1e-9


def test_merge_does_not_mutate_input_and_keeps_first_appearance_order():
    summary, munis = _two_piece_fixture()
    before = summary.copy()

    out = merge_municipality_pieces(summary, munis, gid_col='GID_4')

    pd.testing.assert_frame_equal(summary, before)
    assert out['Municipality'].tolist() == ['Valencia', 'Sagunto']


def _named_summary():
    summary = _summary([
        ('G1', 'PalmadeMallorca', 197.6, 434_786.0, 10.0, 1, 5.0, 0),
        ('G2', 'LucainenadelasTorres', 80.0, 600.0, 3.0, 0, 5.0, 0),
        ('G3', 'ComunidaddeBenataeyTorresde', 4.4, np.nan, 1.0, 0, np.nan, 0),
    ])
    summary['INE code'] = ['07040', '04074', np.nan]
    return summary


def _ine_names():
    return pd.DataFrame({
        'ine_code': ['07040', '04074'],
        'province_name': ['Balears, Illes', 'Almería'],
        'municipality_name': ['Palma', 'Lucainena de las Torres'],
        'population': [434_786, 600],
    })


def test_apply_ine_names_replaces_squashed_gadm_names():
    out = apply_ine_names(_named_summary(), _ine_names()).set_index('GADM ID')

    assert out.loc['G1', 'Municipality'] == 'Palma'
    assert out.loc['G2', 'Municipality'] == 'Lucainena de las Torres'


def test_apply_ine_names_keeps_the_original_and_records_the_source():
    out = apply_ine_names(_named_summary(), _ine_names()).set_index('GADM ID')

    assert out.loc['G2', 'Municipality (GADM name)'] == 'LucainenadelasTorres'
    assert out.loc['G2', 'Name source'] == 'ine'


def test_apply_ine_names_leaves_unmatched_rows_untouched():
    out = apply_ine_names(_named_summary(), _ine_names()).set_index('GADM ID')

    assert out.loc['G3', 'Municipality'] == 'ComunidaddeBenataeyTorresde'      # not guessed
    assert out.loc['G3', 'Name source'] == 'gadm'


def test_apply_ine_names_keeps_bilingual_names_as_ine_writes_them():
    summary = _named_summary().iloc[:1].copy()
    ine = pd.DataFrame({'ine_code': ['07040'], 'province_name': ['Balears, Illes'],
                        'municipality_name': ['Palma/Palma de Mallorca'], 'population': [1]})

    out = apply_ine_names(summary, ine)

    assert out['Municipality'].iloc[0] == 'Palma/Palma de Mallorca'


def test_apply_ine_names_does_not_mutate_input():
    summary = _named_summary()
    before = summary.copy()

    apply_ine_names(summary, _ine_names())

    pd.testing.assert_frame_equal(summary, before)
