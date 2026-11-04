#!/usr/bin/env python3
"""
Cycling-infrastructure maps (interactive HTML + PNG) for the 13 RedBici pilot
cities, classified with the current classify_area_road() taxonomy, then a
PowerPoint and a PDF stacking them (see cycling_analysis/city_report.py).

Needs the regional PBFs and GADM file already cached in --data-dir (a normal
country_analysis.py run downloads them). Per-city results are cached as
parquet under <output-dir>/cache, so re-runs only redo what is missing.

Usage:
    python scripts/city_maps.py
    python scripts/city_maps.py --cities Valencia Bilbao --no-report
"""

import argparse
import os
import sys
from pathlib import Path

import matplotlib
matplotlib.use('Agg')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

import rasterio  # noqa: F401  (before geopandas)
import geopandas as gpd
import pandas as pd

from cycling_analysis.area_classification import CATEGORIES
from cycling_analysis.city_map import (
    build_html_map, km_by_category, load_city_roads, render_map_png,
)
from cycling_analysis.country import ascii_slug

# display name -> (GADM level-4 GID, REGIONS['spain'] name). The GID is pinned because
# GADM has same-named enclave polygons for Alicante, Zaragoza and Valencia (largest = the city).
PILOT_CITIES = {
    'Alicante':                  ('ESP.10.1.1.3_1',  'Comunidad Valenciana'),
    'Vitoria-Gasteiz':           ('ESP.16.1.3.12_1', 'País Vasco'),
    'Vilafranca del Penedès':    ('ESP.6.1.12.32_1', 'Cataluña'),
    'Zaragoza':                  ('ESP.2.3.2.61_1',  'Aragón'),
    'Villena':                   ('ESP.10.1.3.7_1',  'Comunidad Valenciana'),
    'Sant Cugat del Vallès':     ('ESP.6.1.10.15_1', 'Cataluña'),
    'Lloret de Mar':             ('ESP.6.2.6.11_1',  'Cataluña'),
    'Fuenlabrada':               ('ESP.8.1.4.12_1',  'Comunidad de Madrid'),
    'Siero':                     ('ESP.17.1.8.18_1', 'Principado de Asturias'),
    'San Sebastián':             ('ESP.16.2.1.32_1', 'País Vasco'),
    'Bilbao':                    ('ESP.16.3.1.28_1', 'País Vasco'),
    "L'Hospitalet de Llobregat": ('ESP.6.1.11.15_1', 'Cataluña'),
    'Valencia':                  ('ESP.10.3.4.1_1',  'Comunidad Valenciana'),
}


def process_city(city, data_dir, out_dir, force=False):
    gid, region = PILOT_CITIES[city]
    slug = ascii_slug(city).replace("'", '').replace('-', '_')
    cache = out_dir / 'cache' / f'{slug}.parquet'
    cache.parent.mkdir(parents=True, exist_ok=True)

    from cycling_analysis.country import GADM_CONFIG, UTM_CRS_BY_COUNTRY, load_gadm_municipalities
    if cache.exists() and not force:
        cycling = gpd.read_parquet(cache)
        gadm = load_gadm_municipalities('spain', data_dir)
        row = gadm[gadm[f"GID_{GADM_CONFIG['spain']['level']}"] == gid].iloc[0]
        poly_utm = gpd.GeoSeries([row.geometry], crs=gadm.crs).to_crs(UTM_CRS_BY_COUNTRY['spain']).iloc[0]
        print(f'  {city}: loaded from cache ({len(cycling):,} segments)')
    else:
        cycling, poly_utm, _ = load_city_roads('spain', gid, region, data_dir)
        cycling.to_parquet(cache)
        print(f'  {city}: classified {len(cycling):,} segments')

    build_html_map(cycling, poly_utm, city, out_dir / f'{slug}.html')
    render_map_png(cycling, poly_utm, out_dir / f'{slug}_map.png', with_legend=False)
    km = km_by_category(cycling)
    return {'City': city, 'Region': region, 'GADM ID': gid, 'Slug': slug, 'Total km': round(km.sum(), 1),
            **{c: round(km[c], 1) for c in CATEGORIES}}, poly_utm, cycling


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--cities', nargs='*', default=None, choices=list(PILOT_CITIES), help='default: all 13')
    ap.add_argument('--data-dir', default='data')
    ap.add_argument('--output-dir', default='output/city_maps')
    ap.add_argument('--force', action='store_true', help='ignore the per-city cache')
    ap.add_argument('--no-report', action='store_true', help='skip the PPTX/PDF')
    args = ap.parse_args()

    data_dir, out_dir = Path(args.data_dir), Path(args.output_dir)
    cities = args.cities or list(PILOT_CITIES)
    rows, failed = [], []
    for city in cities:
        print(f'[{len(rows) + len(failed) + 1}/{len(cities)}] {city}')
        try:
            row, _, _ = process_city(city, data_dir, out_dir, args.force)
            rows.append(row)
        except Exception as e:  # keep going - one bad city shouldn't lose the others
            failed.append((city, repr(e)))
            print(f'  FAILED: {e!r}')

    summary = pd.DataFrame(rows)
    summary.to_csv(out_dir / 'city_maps_summary.csv', index=False)
    print(f'\nWrote {len(rows)} maps to {out_dir}; failed: {failed or "none"}')

    if rows and not args.no_report and len(rows) == len(PILOT_CITIES):
        from cycling_analysis.city_report import build_pdf, build_pptx
        order = [c for c in PILOT_CITIES if c in set(summary['City'])]
        summary = summary.set_index('City').loc[order].reset_index()
        build_pptx(summary, out_dir, out_dir / 'RedBici_pilot_cycling_maps.pptx')
        build_pdf(summary, out_dir, out_dir / 'RedBici_pilot_cycling_maps.pdf')
        print('Wrote PPTX and PDF')
    elif rows and not args.no_report:
        print('Report skipped: it is only built when all 13 cities succeeded.')


if __name__ == '__main__':
    main()
