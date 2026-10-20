#!/usr/bin/env python3
"""
Country/region-level cycling infrastructure screening, municipality by
municipality (via GADM boundaries).

Replaces cycling_country_analysis.ipynb, where COUNTRY / FILTER_REGIONS were
two module-level variables you edited by hand between runs. Resumable: safe
to Ctrl-C and re-run, it picks up from `{run-name}_checkpoint.csv`.

Usage:
    python scripts/country_analysis.py --country spain --regions Cataluña
    python scripts/country_analysis.py --country netherlands   # whole country
"""

import argparse
import os
import sys
from pathlib import Path
import matplotlib
matplotlib.use('Agg')

sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from cycling_analysis.area_classification import CATEGORIES
from cycling_analysis.bike_amenities import AMENITY_METRIC_COLUMNS
from cycling_analysis.country import REGIONS, run_country_analysis
from cycling_analysis.pollution import POLLUTION_COLUMNS


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--country', required=True, choices=sorted(REGIONS.keys()))
    parser.add_argument('--regions', nargs='*', default=None,
                         help='Sub-region name(s) to run (must match GADM values - '
                              'run once and check the printed diagnostic if unsure). '
                              'Omit to run the whole country.')
    parser.add_argument('--data-dir', default='data')
    parser.add_argument('--max-segments', type=int, default=30_000,
                         help='Skip a municipality if its road network exceeds this many '
                              'segments (guards against RAM blowups from bbox overcounts)')
    parser.add_argument('--pollution-year', type=int, default=2025,
                         help='Year of EEA hourly data to pool for the pollution columns '
                              '(Spain only for now)')
    parser.add_argument('--population-year', type=int, default=2025,
                         help='Year of INE Padrón data to use for the population columns '
                              '(Spain only - ignored for other countries, which use '
                              'WorldPop regardless)')
    args = parser.parse_args()

    summary = run_country_analysis(
        country=args.country,
        filter_regions=args.regions,
        data_dir=Path(args.data_dir),
        max_segments=args.max_segments,
        pollution_year=args.pollution_year,
        population_year=args.population_year,
    )

    print(f'\nShape: {summary.shape[0]} municipalities x {summary.shape[1]} columns')

    n = 10
    print(f'\nTop {n} municipalities by total cycling infrastructure:\n')
    top = summary.nlargest(n, 'Total Cycling Path km')[['Region', 'Municipality', 'Total Cycling Path km']]
    print(top.to_string(index=False))

    cat_cols = [c for c in CATEGORIES if c in summary.columns]
    nat = summary[cat_cols].sum().sort_values(ascending=False)
    total_nat = nat.sum()
    print('\nCategory breakdown:')
    for cat, km in nat.items():
        pct = km / total_nat * 100 if total_nat > 0 else 0
        bar = '#' * int(pct / 2)
        print(f'  {cat:<35} {km:>8.1f} km  {pct:5.1f}%  {bar}')

    if 'Municipality area (km²)' in summary.columns:
        print(f"\nTotal area covered: {summary['Municipality area (km²)'].sum():,.1f} km²")

    amenity_cols = [c for c in AMENITY_METRIC_COLUMNS if c in summary.columns]
    if amenity_cols:
        print('\nBike amenities (nationwide totals):')
        for col in amenity_cols:
            print(f'  {col:<40} {summary[col].sum():>10,.0f}')

    pollution_cols = [c for c in POLLUTION_COLUMNS if c in summary.columns]
    if pollution_cols:
        n_with_stations = (summary['Station count'] > 0).sum()
        print(f'\nPollution columns: {n_with_stations}/{len(summary)} municipalities have >=1 station')
        for col in pollution_cols:
            if col == 'Station count':
                continue
            valid = summary[col].dropna()
            if len(valid):
                print(f'  {col:<35} median {valid.median():>6.1f}  '
                      f'(n={len(valid)}, range {valid.min():.1f}-{valid.max():.1f})')


if __name__ == '__main__':
    main()
