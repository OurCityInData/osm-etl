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
"""

import numpy as np
import pandas as pd

from .area_classification import CATEGORIES
from .bike_amenities import AMENITY_METRIC_COLUMNS
from .ine import municipality_group_key

AREA_COL = 'Municipality area (km²)'
STATION_COL = 'Station count'
EXTRA_COLUMNS = ['INE code', 'GADM IDs', 'Parts']


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
