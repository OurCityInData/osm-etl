"""
One output row per municipality (Spain).

GADM stores some municipalities as several polygon rows - a main body plus
small separate pieces (islands, exclaves, parts in another comarca; see
ine.py's module docstring for the numbers). The pipeline still processes each
polygon on its own (checkpoints are keyed by GADM ID and stay valid), but the
tables people read should have one row per municipality.
merge_municipality_pieces() folds a split municipality's rows into one:

  summed over the pieces   every km category, the bike-amenity counts,
                           Municipality area (km2) and Station count - the
                           pieces are disjoint areas, so these are additive
  taken from the largest   Region, Municipality (name), GADM ID, the pollution
  piece                    medians and exceedance-day counts (per-municipality
                           statistics, not additive)
  recomputed               Population density = Population / merged area
  Population               the one non-NaN value among the pieces (ine.py
                           places it on a single piece), so it is never summed

Three bookkeeping columns are appended to the merged rows: `INE code`,
`GADM IDs` (every piece, ';'-joined, largest first) and `Parts` (1 for an
unsplit municipality). Rows of unsplit municipalities pass through unchanged.

Readable names (apply_ine_names, added 2026-11): GADM strips the spaces from
many Spanish names ('PalmadeMallorca', 'LucainenadelasTorres'; 3,402 of 8,209
rows) and cuts some off at ~27 characters, so `Municipality` is replaced by
INE's name wherever the row has an INE code, with the original kept in
`Municipality (GADM name)` and the provenance in `Name source` ('ine' / 'gadm').
Bilingual INE names ('Valencia/València') are kept exactly as INE writes them.
Rows with no INE code keep GADM's name, spaces and all - nothing is guessed.
"""

import numpy as np
import pandas as pd

from .area_classification import CATEGORIES
from .bike_amenities import AMENITY_METRIC_COLUMNS
from .ine import ine_names_by_code, municipality_group_key

AREA_COL = 'Municipality area (km²)'
STATION_COL = 'Station count'
GADM_NAME_COL = 'Municipality (GADM name)'
NAME_SOURCE_COL = 'Name source'
EXTRA_COLUMNS = ['INE code', 'GADM IDs', 'Parts', GADM_NAME_COL, NAME_SOURCE_COL]


def merge_municipality_pieces(summary: pd.DataFrame, municipalities, *, gid_col: str) -> pd.DataFrame:
    """
    summary: per-polygon result rows, with a 'GADM ID' column (and, before the
        Total column is added, the category/amenity/pollution columns).
    municipalities: the GADM frame used for the run, with `gid_col`, NAME_2,
        NAME_4 and (after ine.match_ine_to_gadm) `ine_code` - it supplies which
        rows are pieces of the same municipality.
    Returns a new frame (input untouched) with one row per municipality, in the
    order each municipality first appears in `summary`.
    """
    keys = municipality_group_key(municipalities)
    key_by_gid = pd.Series(keys.to_numpy(), index=municipalities[gid_col].to_numpy())
    code_by_gid = (pd.Series(municipalities['ine_code'].to_numpy(), index=municipalities[gid_col].to_numpy())
                   if 'ine_code' in municipalities.columns else None)

    df = summary.copy()
    # A row with no key (should not happen) is treated as its own municipality.
    df['_key'] = df['GADM ID'].map(key_by_gid).fillna(pd.Series(('gid:' + df['GADM ID'].astype(str)), index=df.index))
    df['_order'] = np.arange(len(df))
    sizes = df.groupby('_key')['_key'].transform('size')

    sum_cols = [c for c in CATEGORIES + AMENITY_METRIC_COLUMNS + [AREA_COL, STATION_COL] if c in df.columns]
    merged_rows = []
    for key, grp in df[sizes > 1].groupby('_key', sort=False):
        grp = grp.sort_values(AREA_COL, ascending=False, kind='stable')
        row = grp.iloc[0].copy()                          # largest piece supplies the non-additive columns
        for c in sum_cols:
            row[c] = grp[c].sum()
        row['Population'] = grp['Population'].max()       # one non-NaN value (or all NaN)
        area = row[AREA_COL]
        row['Population density (per km²)'] = round(row['Population'] / area, 2) if area else np.nan
        row['_order'] = grp['_order'].min()
        row['GADM IDs'] = ';'.join(grp['GADM ID'].astype(str))
        row['Parts'] = len(grp)
        merged_rows.append(row)

    singles = df[sizes == 1].copy()
    singles['GADM IDs'] = singles['GADM ID'].astype(str)
    singles['Parts'] = 1
    out = pd.concat([singles, pd.DataFrame(merged_rows)], ignore_index=True) if merged_rows else singles
    out = out.sort_values('_order', kind='stable').reset_index(drop=True)
    out['INE code'] = out['GADM ID'].map(code_by_gid) if code_by_gid is not None else np.nan
    out['Parts'] = out['Parts'].astype(int)
    return out.drop(columns=['_key', '_order'])


def apply_ine_names(summary: pd.DataFrame, ine_df: pd.DataFrame) -> pd.DataFrame:
    """
    Replace `Municipality` with INE's reading-order name where the row has an
    `INE code` (see ine.ine_display_name); keep the original GADM name in
    `Municipality (GADM name)` and record `Name source` ('ine' or 'gadm').
    Returns a copy; rows without an INE code are left exactly as they were.
    """
    out = summary.copy()
    out[GADM_NAME_COL] = out['Municipality']
    ine_name = out['INE code'].map(ine_names_by_code(ine_df))
    has_ine = ine_name.notna()
    out.loc[has_ine, 'Municipality'] = ine_name[has_ine]
    out[NAME_SOURCE_COL] = np.where(has_ine, 'ine', 'gadm')
    return out
