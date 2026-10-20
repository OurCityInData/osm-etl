"""
INE (Instituto Nacional de Estadística) municipal population data - Spain only.

Replaces the WorldPop-raster population source (population.py) for Spain with
INE's official annual Padrón municipal (municipal register) - real census-
register counts, not a modelled raster estimate. This is a genuine accuracy
upgrade for Spain specifically (whole-number, government-published counts vs.
a zonal-summed fractional raster estimate - see population.py's docstring for
why WorldPop values come out as e.g. 1075.827148), but it does NOT generalise
to the other 4 configured countries (Netherlands/Germany/Belgium/Denmark) -
INE is Spain's national statistics institute and has no equivalent for them.
country.py dispatches on this: Spain uses this module, everyone else keeps
using population.py's WorldPop path. This mirrors pollution.py's existing
POLLUTION_COUNTRIES gate rather than inventing a new pattern.

Source: INE's bulk "pobmun" file - one static zip covering every Spanish
municipality for every year back to 1996, verified live (2026-09):
https://www.ine.es/pob_xls/pobmun.zip
  -> pobmun/pobmun{YY}.xlsx (e.g. pobmun25.xlsx for 2025), single sheet
     named 'dic{YY}', header on the 2nd row, columns:
     CPRO (2-digit province code), PROVINCIA (province name),
     CMUN (3-digit municipality code within province), NOMBRE (municipality
     name), POB{YY} (population, whole number), HOMBRES, MUJERES.
This is a single bulk download (like the WorldPop raster or the EEA zone
zip), not per-municipality API calls.

IMPORTANT - "province" here is NOT GADM_CONFIG['spain']['province_col']:
GADM_CONFIG['spain']['province_col'] is 'NAME_1' (the autonomous-community
level used for REGIONS['spain'] filtering elsewhere in this codebase, e.g.
'Cataluña'). INE's PROVINCIA field is Spain's true 52-province administrative
level (e.g. 'Barcelona', 'Girona' - the provinces *within* Cataluña), which
in GADM's level-4 hierarchy is a different, finer column: NAME_2. Matching
against the wrong column silently produces near-zero matches - use NAME_2
here, never NAME_1.

Matching INE to GADM municipalities (the hard part):
GADM's Spain data carries no usable municipality code at all (CC_4 is 'NA'
for every row - verified 2026-09), so there is no shared numeric key. The
join has to be by normalized (province, municipality) name pair, and two
real naming-convention mismatches had to be found and handled (verified
empirically against the live GADM cache and INE file, not assumed):

1. INE alphabetizes some Spanish names as "Base, Article" for sorting
   (e.g. 'Rioja, La', 'Yebenes, Los', 'Mercadal, Es') while GADM stores them
   in natural reading order ('La Rioja', 'Los Yebenes', 'Es Mercadal').
   _reorder_trailing_article() detects a trailing ", <article>" suffix
   (Castilian: El/La/Los/Las/Lo; Catalan/Balearic: Els/Les/Illes/Es/Sa/Ses;
   Galician: A; elided Catalan: L') and moves it to the front before
   normalizing.
2. Several provinces and municipalities have distinct co-official-language
   names that are NOT reducible to accents/word-order - a plain normalize
   can't bridge them. PROVINCE_NAME_OVERRIDES hardcodes the 7 provinces
   where this happens (verified against all 52 GADM/INE province name pairs
   directly, not guessed):
     Alicante/Alacant, Castellón/Castelló, Valencia/València -> take the
       Castilian (first) half - GADM keeps the Castilian name for these 3.
     Araba/Álava -> GADM keeps the Basque name ('Álava') for this one -
       opposite of the 3 above, hence a dedicated override, not a "take
       first half" rule.
     Bizkaia -> Vizcaya, Gipuzkoa -> Guipúzcoa: GADM uses the Castilian
       province name; INE uses the Basque one. Different words entirely,
       not an accent/order difference.
     Balears, Illes -> Baleares: same "different word" case, plus needs
       the article-reorder step first ('Balears, Illes' -> 'Illes Balears').
   At municipality level, the same bilingual-name pattern recurs for
   hundreds of individual municipalities (Basque/Catalan/Galician co-
   official names, e.g. 'Xabia/Javea') - hardcoding every one the way the
   52 provinces were is impractical, so _municipality_candidates() instead
   tries both halves of a '/'-separated name generically.

Verified match rate against a live GADM ESP level-4 pull + pobmun25.xlsx
(2026-09): 7,963 / 8,132 municipalities matched (97.9%). The remaining ~2%
(169 municipalities, 2025 data) are a genuine residual gap - a long tail of
one-off spelling differences, apostrophe-article edge cases beyond the
patterns above, and likely a few municipality mergers/renames where GADM's
vintage lags INE's - see CLAUDE.md's Known gaps. match_ine_to_gadm() prints
every unmatched (province, municipality) pair rather than silently dropping
them, and leaves `population`/`population_density` as NaN (not 0.0) for
those rows - save_table_png() already renders NaN as a blank cell, so this
degrades to "no data shown" rather than a misleading zero.
"""

import re
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import pandas as pd
import requests

INE_POBMUN_URL = 'https://www.ine.es/pob_xls/pobmun.zip'

# Trailing ", <article>" alphabetization suffix INE uses for some province
# and municipality names (Castilian/Catalan/Balearic/Galician articles).
# Order matters only in that longer alternatives (e.g. 'Illes' vs 'Les')
# don't collide here - none do.
_ARTICLE_SUFFIX = re.compile(
    r"^(.*),\s*(El|La|Los|Las|Lo|Els|Les|Illes|Es|Sa|Ses|A|L')$",
    re.IGNORECASE,
)

# INE province name -> GADM NAME_2 value, normalized, for the 7 provinces
# where the two sources use genuinely different words (not just accents/
# word order) - see module docstring for how each was verified.
PROVINCE_NAME_OVERRIDES = {
    'alicantealacant': 'alicante',
    'castelloncastello': 'castellon',
    'valenciavalencia': 'valencia',
    'arabaalava': 'alava',
    'bizkaia': 'vizcaya',
    'gipuzkoa': 'guipuzcoa',
    'illesbalears': 'baleares',
}


def _reorder_trailing_article(name: str) -> str:
    """'Rioja, La' -> 'La Rioja'; leaves names with no trailing article alone."""
    m = _ARTICLE_SUFFIX.match(str(name).strip())
    if not m:
        return name
    base, article = m.group(1).strip(), m.group(2).strip()
    sep = '' if article.endswith("'") else ' '
    return f'{article}{sep}{base}'


def _normalize(s: str) -> str:
    """Lowercase, strip accents, drop every non-alphanumeric character."""
    s = unicodedata.normalize('NFKD', str(s)).encode('ascii', 'ignore').decode()
    return ''.join(ch for ch in s.lower().strip() if ch.isalnum())


def _normalize_province(name: str) -> str:
    n = _normalize(_reorder_trailing_article(name))
    return PROVINCE_NAME_OVERRIDES.get(n, n)


def _municipality_candidates(name: str) -> set:
    """
    Normalized forms to try for one INE municipality name: the whole name,
    plus (for a bilingual 'Spanish/Regional' name) each half separately -
    GADM sometimes keeps the Castilian half, sometimes the regional half,
    with no reliable rule (see module docstring), so try both.
    """
    reordered = _reorder_trailing_article(name)
    cands = {_normalize(reordered)}
    if '/' in str(name):
        cands.update(_normalize(part) for part in str(name).split('/'))
    return cands


def fetch_ine_population(year: int, data_dir: Path) -> pd.DataFrame:
    """
    Downloads (if not cached) INE's pobmun bulk file and returns that year's
    municipal population table with columns
    ['province_name', 'municipality_name', 'population'].

    year: e.g. 2025 -> reads pobmun/pobmun25.xlsx, sheet 'dic25', column
    'POB25'. Cached to data_dir/pobmun.zip (whole-file cache, like the
    WorldPop raster / EEA zone zip elsewhere in this codebase) plus a
    per-year data_dir/pobmun{yy}.xlsx extract.
    """
    data_dir = Path(data_dir)
    yy = f'{year % 100:02d}'
    zip_cache = data_dir / 'pobmun.zip'
    xlsx_cache = data_dir / f'pobmun{yy}.xlsx'

    if not xlsx_cache.exists():
        if not zip_cache.exists():
            print('Downloading INE pobmun.zip (municipal population, all years) ...')
            r = requests.get(INE_POBMUN_URL, timeout=120)
            r.raise_for_status()
            zip_cache.write_bytes(r.content)
        member = f'pobmun/pobmun{yy}.xlsx'
        with zipfile.ZipFile(zip_cache) as z:
            if member not in z.namelist():
                raise ValueError(
                    f'{member} not found in pobmun.zip - INE may not have '
                    f'published year {year} yet, or the internal path changed. '
                    f'Available: {z.namelist()}'
                )
            xlsx_cache.write_bytes(z.read(member))

    df = pd.read_excel(
        xlsx_cache, sheet_name=f'dic{yy}', header=1,
        dtype={'CPRO': str, 'CMUN': str},
    )
    df = df.rename(columns={
        'PROVINCIA': 'province_name',
        'NOMBRE': 'municipality_name',
        f'POB{yy}': 'population',
    })
    return df[['province_name', 'municipality_name', 'population']]


def match_ine_to_gadm(municipalities_gdf: gpd.GeoDataFrame, ine_df: pd.DataFrame) -> gpd.GeoDataFrame:
    """
    Joins INE's population table onto municipalities_gdf by normalized
    (province, municipality) name - see module docstring for the matching
    rules and their verified 97.9% hit rate. Expects municipalities_gdf to
    carry GADM's NAME_2 (province) and NAME_4 (municipality) columns -
    Spain-only, unlike population.py's compute_population_stats() which is
    generic across all 5 countries.

    Adds a `population` column (float, NaN where unmatched - never 0.0, so
    a real-but-unmatched municipality isn't confused with a genuinely empty
    one). Returns a copy; does not mutate the input in place. Prints every
    unmatched (province, municipality) pair rather than dropping them
    silently.
    """
    lookup = {}
    for _, row in ine_df.iterrows():
        prov_key = _normalize_province(row['province_name'])
        for muni_key in _municipality_candidates(row['municipality_name']):
            lookup[(prov_key, muni_key)] = row['population']

    out = municipalities_gdf.copy()
    prov_keys = out['NAME_2'].apply(_normalize_province)
    muni_keys = out['NAME_4'].apply(lambda s: _normalize(_reorder_trailing_article(s)))
    out['population'] = [
        lookup.get((p, m), float('nan')) for p, m in zip(prov_keys, muni_keys)
    ]

    unmatched = out[out['population'].isna()]
    if len(unmatched) > 0:
        pct = len(unmatched) / len(out) * 100
        print(f'WARNING: {len(unmatched)}/{len(out)} municipalities ({pct:.1f}%) had no INE '
              f'population match - Population/Population density left blank for these '
              f'(see cycling_analysis/ine.py\'s module docstring and CLAUDE.md Known gaps).')
        sample = list(zip(unmatched['NAME_2'], unmatched['NAME_4']))[:10]
        print(f'  Sample unmatched (province, municipality): {sample}')

    return out
