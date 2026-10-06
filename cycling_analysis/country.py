"""
Country-level cycling-infrastructure screening.

Ported from cycling_country_analysis.ipynb. Classification itself lives in
area_classification.py (classify_area_road(), a single area-adjustable
taxonomy driven by CyclingLegalConfig) — not the same as
classify_cycling_path_amsterdam or classify_cycling_path_bcn in
classification.py. This module exists for a different purpose: coarse,
nationwide screening across every municipality in a country/region (via
GADM boundaries), not a single-city high-precision comparison against an
official reference dataset. Kept as its own module rather than forced into
classification.py.

The original notebook processed one region/country per run via two
module-level variables you edited by hand (COUNTRY, FILTER_REGIONS) and
wrote a bare CHECKPOINT_PATH/RUN_NAME csv into the working directory.
run_country_analysis() below is the same municipality-by-municipality,
checkpoint-resumable loop, but as a function taking those as parameters
(see scripts/country_analysis.py for the CLI).
"""

import gc
import io
import os
import re
import subprocess
import tempfile
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from pyrosm import OSM

from .area_classification import (
    CATEGORIES,
    EXTRA_OSM_ATTRIBUTES,
    classify_area_road,
    get_cycling_config,
)
from .bike_amenities import (
    AMENITY_METRIC_COLUMNS,
    BIKE_AMENITY_EXTRA_ATTRIBUTES,
    BIKE_AMENITY_FILTER,
    compute_bike_amenity_stats,
)
from .pollution import (
    POLLUTION_COLUMNS,
    compute_daily_max_hourly,
    compute_daily_means,
    compute_zone_pollution_stats,
    estimate_own_pollution_stats,
    fetch_eea_measurements,
    fetch_eea_stations,
    fetch_zone_geometries,
    match_municipality_to_zones,
    match_stations_to_municipality,
    match_stations_to_zones,
)
from .population import (
    POPULATION_COLUMNS,
    compute_population_stats,
    fetch_population_raster,
)

# pollution.py is Spain-only for now (see CLAUDE.md's Known gaps) - every
# other country gets blank/NaN columns 1-10, same as a Spanish municipality
# with 0 stations.
POLLUTION_COUNTRIES = {'spain': 'ES'}

# ── Sub-region PBF sources ───────────────────────────────────────────────────
# Each entry: (sub_region_name, geofabrik_url). sub_region_name must match the
# GADM province_col value for that region (see GADM_CONFIG below) — mismatches
# cause that region to be silently skipped, so verify against
# load_gadm_municipalities() output before a big run.
REGIONS = {
    'spain': [
        ('Andalucía',         'https://download.geofabrik.de/europe/spain/andalucia-latest.osm.pbf'),
        ('Aragón',            'https://download.geofabrik.de/europe/spain/aragon-latest.osm.pbf'),
        ('Asturias',          'https://download.geofabrik.de/europe/spain/asturias-latest.osm.pbf'),
        ('Cantabria',         'https://download.geofabrik.de/europe/spain/cantabria-latest.osm.pbf'),
        ('Castilla-La Mancha','https://download.geofabrik.de/europe/spain/castilla-la-mancha-latest.osm.pbf'),
        ('Castilla y León',   'https://download.geofabrik.de/europe/spain/castilla-y-leon-latest.osm.pbf'),
        ('Cataluña',          'https://download.geofabrik.de/europe/spain/cataluna-latest.osm.pbf'),
        ('Extremadura',       'https://download.geofabrik.de/europe/spain/extremadura-latest.osm.pbf'),
        ('Galicia',           'https://download.geofabrik.de/europe/spain/galicia-latest.osm.pbf'),
        ('Islas Baleares',    'https://download.geofabrik.de/europe/spain/islas-baleares-latest.osm.pbf'),
        ('La Rioja',          'https://download.geofabrik.de/europe/spain/la-rioja-latest.osm.pbf'),
        ('Madrid',            'https://download.geofabrik.de/europe/spain/madrid-latest.osm.pbf'),
        ('Región de Murcia',  'https://download.geofabrik.de/europe/spain/murcia-latest.osm.pbf'),
        ('Navarra',           'https://download.geofabrik.de/europe/spain/navarra-latest.osm.pbf'),
        ('País Vasco',        'https://download.geofabrik.de/europe/spain/pais-vasco-latest.osm.pbf'),
        ('Valencia',          'https://download.geofabrik.de/europe/spain/valencia-latest.osm.pbf'),
    ],
    'netherlands': [
        ('Groningen',     'https://download.geofabrik.de/europe/netherlands/groningen-latest.osm.pbf'),
        ('Friesland',     'https://download.geofabrik.de/europe/netherlands/friesland-latest.osm.pbf'),
        ('Drenthe',       'https://download.geofabrik.de/europe/netherlands/drenthe-latest.osm.pbf'),
        ('Overijssel',    'https://download.geofabrik.de/europe/netherlands/overijssel-latest.osm.pbf'),
        ('Flevoland',     'https://download.geofabrik.de/europe/netherlands/flevoland-latest.osm.pbf'),
        ('Gelderland',    'https://download.geofabrik.de/europe/netherlands/gelderland-latest.osm.pbf'),
        ('Utrecht',       'https://download.geofabrik.de/europe/netherlands/utrecht-latest.osm.pbf'),
        ('Noord-Holland', 'https://download.geofabrik.de/europe/netherlands/noord-holland-latest.osm.pbf'),
        ('Zuid-Holland',  'https://download.geofabrik.de/europe/netherlands/zuid-holland-latest.osm.pbf'),
        ('Zeeland',       'https://download.geofabrik.de/europe/netherlands/zeeland-latest.osm.pbf'),
        ('Noord-Brabant', 'https://download.geofabrik.de/europe/netherlands/noord-brabant-latest.osm.pbf'),
        ('Limburg',       'https://download.geofabrik.de/europe/netherlands/limburg-latest.osm.pbf'),
    ],
    'germany': [
        ('Baden-Württemberg',      'https://download.geofabrik.de/europe/germany/baden-wuerttemberg-latest.osm.pbf'),
        ('Bayern',                 'https://download.geofabrik.de/europe/germany/bayern-latest.osm.pbf'),
        ('Berlin',                 'https://download.geofabrik.de/europe/germany/berlin-latest.osm.pbf'),
        ('Brandenburg',            'https://download.geofabrik.de/europe/germany/brandenburg-latest.osm.pbf'),
        ('Bremen',                 'https://download.geofabrik.de/europe/germany/bremen-latest.osm.pbf'),
        ('Hamburg',                'https://download.geofabrik.de/europe/germany/hamburg-latest.osm.pbf'),
        ('Hessen',                 'https://download.geofabrik.de/europe/germany/hessen-latest.osm.pbf'),
        ('Mecklenburg-Vorpommern', 'https://download.geofabrik.de/europe/germany/mecklenburg-vorpommern-latest.osm.pbf'),
        ('Niedersachsen',          'https://download.geofabrik.de/europe/germany/niedersachsen-latest.osm.pbf'),
        ('Nordrhein-Westfalen',    'https://download.geofabrik.de/europe/germany/nordrhein-westfalen-latest.osm.pbf'),
        ('Rheinland-Pfalz',        'https://download.geofabrik.de/europe/germany/rheinland-pfalz-latest.osm.pbf'),
        ('Saarland',               'https://download.geofabrik.de/europe/germany/saarland-latest.osm.pbf'),
        ('Sachsen',                'https://download.geofabrik.de/europe/germany/sachsen-latest.osm.pbf'),
        ('Sachsen-Anhalt',         'https://download.geofabrik.de/europe/germany/sachsen-anhalt-latest.osm.pbf'),
        ('Schleswig-Holstein',     'https://download.geofabrik.de/europe/germany/schleswig-holstein-latest.osm.pbf'),
        ('Thüringen',              'https://download.geofabrik.de/europe/germany/thueringen-latest.osm.pbf'),
    ],
    # Geofabrik has NO sub-regions for Belgium -> URLs are None; the country
    # PBF (COUNTRY_PBFS) is downloaded once and osmium-extracted per municipality.
    'belgium': [
        ('Antwerp',         None),
        ('East Flanders',   None),
        ('West Flanders',   None),
        ('Flemish Brabant', None),
        ('Brussels',        None),
        ('Hainaut',         None),
        ('Liège',           None),
        ('Limburg',         None),
        ('Luxembourg',      None),
        ('Namur',           None),
        ('Walloon Brabant', None),
    ],
    'denmark': [
        ('Nordjylland', 'https://download.geofabrik.de/europe/denmark/nordjylland-latest.osm.pbf'),
        ('Midtjylland', 'https://download.geofabrik.de/europe/denmark/midtjylland-latest.osm.pbf'),
        ('Syddanmark',  'https://download.geofabrik.de/europe/denmark/syddanmark-latest.osm.pbf'),
        ('Sjælland',    'https://download.geofabrik.de/europe/denmark/sjaelland-latest.osm.pbf'),
        ('Hovedstaden', 'https://download.geofabrik.de/europe/denmark/hovedstaden-latest.osm.pbf'),
    ],
}

COUNTRY_PBFS = {
    'belgium': 'https://download.geofabrik.de/europe/belgium-latest.osm.pbf',
}

GADM_CODES = {
    'spain': 'ESP', 'netherlands': 'NLD', 'germany': 'DEU',
    'belgium': 'BEL', 'denmark': 'DNK',
}

GADM_CONFIG = {
    'spain':       {'level': 4, 'province_col': 'NAME_1', 'muni_col': 'NAME_4'},
    'netherlands': {'level': 2, 'province_col': 'NAME_1', 'muni_col': 'NAME_2'},
    'germany':     {'level': 2, 'province_col': 'NAME_1', 'muni_col': 'NAME_2'},
    'belgium':     {'level': 4, 'province_col': 'NAME_2', 'muni_col': 'NAME_4'},
    'denmark':     {'level': 2, 'province_col': 'NAME_1', 'muni_col': 'NAME_2'},
}

# UTM zone per country. EPSG:32631 covers Spain/Netherlands/Belgium/France;
# use EPSG:32632 for Denmark/eastern Germany if you add those runs.
UTM_CRS_BY_COUNTRY = {
    'spain': 'EPSG:32631', 'netherlands': 'EPSG:32631',
    'belgium': 'EPSG:32631', 'germany': 'EPSG:32632', 'denmark': 'EPSG:32632',
}

MAX_SEGMENTS = 30_000


def ascii_slug(s: str) -> str:
    """Strip accents so filenames stay ASCII-safe (e.g. 'Cataluña' -> 'cataluna')."""
    return unicodedata.normalize('NFD', s).encode('ascii', 'ignore').decode().lower().replace(' ', '_')


def load_gadm_municipalities(country: str, data_dir: Path) -> gpd.GeoDataFrame:
    """
    Downloads GADM boundaries at the configured level. Drops rows where the
    municipality name column is null (GADM sometimes has null names for
    aggregate polygons). Cached locally after first download.
    """
    code = GADM_CODES[country]
    level = GADM_CONFIG[country]['level']
    mcol = GADM_CONFIG[country]['muni_col']
    cache = data_dir / f'gadm41_{code}_{level}.geojson'

    if cache.exists():
        print(f'Cached: {cache.name}')
        gdf = gpd.read_file(cache)
    else:
        url = f'https://geodata.ucdavis.edu/gadm/gadm4.1/json/gadm41_{code}_{level}.json.zip'
        print(f'Downloading GADM level-{level} for {code} ...')
        r = requests.get(url, timeout=300)
        r.raise_for_status()
        with zipfile.ZipFile(io.BytesIO(r.content)) as z:
            json_file = [n for n in z.namelist() if n.lower().endswith('.json')][0]
            gdf = gpd.read_file(z.open(json_file))
        gdf.to_file(cache, driver='GeoJSON')
        print(f'Downloaded {len(gdf)} rows -> cached to {cache.name}')

    before = len(gdf)
    gdf = gdf[gdf[mcol].notna()].copy()
    if before - len(gdf) > 0:
        print(f'Dropped {before - len(gdf)} rows with null {mcol}')
    return gdf


def download_pbf(url: str, dest: Path) -> Path:
    if dest.exists():
        return dest
    r = requests.get(url, stream=True, timeout=300)
    r.raise_for_status()
    total = int(r.headers.get('content-length', 0))
    downloaded = 0
    with open(dest, 'wb') as f:
        for block in r.iter_content(chunk_size=4 * 1024 * 1024):
            f.write(block)
            downloaded += len(block)
            if total:
                print(f'\r  {downloaded * 100 // total:3d}%', end='', flush=True)
    print()
    return dest


def osmium_extract(src_pbf: str, bbox, dest_pbf: str) -> bool:
    minx, miny, maxx, maxy = bbox
    cmd = (
        f'osmium extract '
        f'-b {minx:.6f},{miny:.6f},{maxx:.6f},{maxy:.6f} '
        f'"{src_pbf}" -o "{dest_pbf}" --overwrite'
    )
    result = subprocess.run(cmd, shell=True, capture_output=True, text=True)
    return result.returncode == 0


def save_table_png(df: pd.DataFrame, out_path: Path, title: str = None) -> None:
    """
    Render a DataFrame as a table image. Uses the Agg canvas directly
    (rather than pyplot) so this doesn't touch global matplotlib backend
    state - safe to call from a library module regardless of what backend
    the calling script has configured.
    """
    n_rows, n_cols = df.shape
    col_labels = list(df.columns)

    def _cell(v):
        if isinstance(v, (int, float, np.floating)):
            return '' if pd.isna(v) else f'{v:,.1f}'
        return str(v)

    cell_text = [[_cell(v) for v in row] for row in df.itertuples(index=False)]

    # Column headers (e.g. "Pacified street at 20 km/h") are usually far
    # longer than the numbers underneath them - size each column off the
    # longest string it actually has to hold (header or value), not
    # matplotlib's default per-cell auto-width, which sizes off cell
    # content only and causes long headers to overlap neighboring columns.
    col_char_widths = [
        max(len(label), max((len(row[i]) for row in cell_text), default=0))
        for i, label in enumerate(col_labels)
    ]
    total_chars = sum(col_char_widths)
    col_fracs = [w / total_chars for w in col_char_widths]

    fig_width = max(10, total_chars * 0.12)
    fig_height = max(2, 0.32 * (n_rows + 1) + (0.6 if title else 0))

    fig = Figure(figsize=(fig_width, fig_height))
    FigureCanvasAgg(fig)
    ax = fig.add_subplot(111)
    ax.axis('off')
    if title:
        ax.set_title(title, fontsize=13, fontweight='bold', pad=14)

    table = ax.table(
        cellText=cell_text,
        colLabels=col_labels,
        colWidths=col_fracs,
        cellLoc='right',
        loc='center',
    )
    table.auto_set_font_size(False)
    table.set_fontsize(9)
    table.scale(1, 1.4)

    text_col_count = sum(1 for dtype in df.dtypes if dtype == object)
    for (r, c), cell in table.get_celld().items():
        cell.set_edgecolor('#dddddd')
        if r == 0:
            cell.set_facecolor('#2b6cb0')
            cell.set_text_props(color='white', fontweight='bold')
        else:
            cell.set_facecolor('#f2f2f2' if r % 2 == 0 else 'white')
        if c < text_col_count:
            cell.set_text_props(ha='left')

    fig.savefig(out_path, dpi=150, bbox_inches='tight')


def slugify_column(name: str) -> str:
    """
    Display column name -> snake_case ASCII identifier for save_database_csv()
    (e.g. 'PM2.5 median annual (µg/m³)' -> 'pm2_5_median_annual_ug_m3') - unlike
    ascii_slug() above (which strips accents from proper nouns for filenames),
    this also has to survive unit/symbol-heavy metric names (µ, ², ³, /, %) that
    most database column-name rules reject. A leading-digit result (e.g.
    '2-1 road' -> '_2_1_road') gets a leading underscore, since SQL generally
    requires identifiers not to start with a digit.
    """
    name = name.replace('µ', 'u').replace('²', '2').replace('³', '3')
    name = unicodedata.normalize('NFKD', name).encode('ascii', 'ignore').decode()
    name = re.sub(r'[^a-zA-Z0-9]+', '_', name).strip('_').lower()
    return f'_{name}' if name[:1].isdigit() else name


def save_database_csv(summary: pd.DataFrame, out_path: Path, *,
                       country: str, run_name: str, pollution_year: int = None) -> None:
    """
    The same municipality-by-municipality data as the main CSV/PNG, reshaped
    for later ingestion into a webapp/backend/database rather than for human
    reading: snake_case ASCII column names (slugify_column()) instead of
    display strings with units/symbols/spaces, plus a few columns a database
    table would need that the display table doesn't:
    - `country`, `run_name`, `pollution_year`, `generated_at` (UTC, ISO 8601) -
      constant across every row in one run, but necessary once rows from
      multiple runs/countries/dates end up pooled in one table.
    - `gadm_id` (from the `GADM ID` column already carried through
      run_country_analysis() - GADM's own stable per-municipality ID, e.g.
      'ESP.1.1.1.1_1') as a real primary/foreign key - `region`+`municipality`
      display names are NOT reliable join keys (GADM strips spaces from some
      Spanish region/municipality names inconsistently, and accents vary by
      source).
    """
    db_df = summary.rename(columns={c: slugify_column(c) for c in summary.columns})
    db_df.insert(0, 'generated_at', pd.Timestamp.now(tz='UTC').isoformat())
    db_df.insert(0, 'pollution_year', pollution_year)
    db_df.insert(0, 'run_name', run_name)
    db_df.insert(0, 'country', country)
    db_df.to_csv(out_path, index=False)


def run_country_analysis(country, filter_regions=None, data_dir=Path('data'),
                          max_segments=MAX_SEGMENTS, run_name=None, pollution_year=2025):
    """
    Municipality-by-municipality cycling infrastructure screen, resumable via
    a `{run_name}_checkpoint.csv` file (safe to Ctrl-C and re-run).

    country: one of REGIONS.keys() ('spain', 'netherlands', 'germany', 'belgium', 'denmark')
    filter_regions: list of sub-region names to run (must match GADM province_col
        values - run once with a bad name to see the diagnostic mismatch printout),
        or None for the whole country.
    pollution_year: year of EEA hourly data to pool for the pollution columns
        (Spain only - see POLLUTION_COUNTRIES). Ignored for other countries.
    Returns the summary DataFrame (including `GADM ID`) - note the CSV/PNG written
    to `{run_name}_cycling_by_municipality.csv`/`.png` drop that column since it's
    an internal join/resume key, not something a human reader needs; the
    database-ready `{run_name}_municipalities.csv` keeps it as `gadm_id`.
    """
    data_dir = Path(data_dir)
    data_dir.mkdir(exist_ok=True, parents=True)
    utm_crs = UTM_CRS_BY_COUNTRY[country]

    all_regions = REGIONS[country]
    if filter_regions:
        province_list = [(name, url) for name, url in all_regions if name in filter_regions]
        missing = set(filter_regions) - {name for name, _ in province_list}
        if missing:
            print(f'WARNING: filter_regions entries not found in REGIONS config: {missing}')
            print(f'   Available: {[name for name, _ in all_regions]}')
    else:
        province_list = all_regions

    country_mode = country in COUNTRY_PBFS
    country_pbf_path = data_dir / COUNTRY_PBFS[country].split('/')[-1] if country_mode else None

    prov_col = GADM_CONFIG[country]['province_col']
    muni_col = GADM_CONFIG[country]['muni_col']
    gid_col = f"GID_{GADM_CONFIG[country]['level']}"

    if run_name is None:
        if filter_regions:
            region_tag = '_'.join(ascii_slug(r) for r in sorted(filter_regions))
            run_name = f'{country}_{region_tag}'
        else:
            run_name = country

    print(f'Country        : {country}')
    print(f'Regions to run : {[name for name, _ in province_list]}')
    print(f'Mode           : {"country PBF" if country_mode else "per-region PBFs"}')
    print(f'Run name       : {run_name}')

    municipalities_gdf = load_gadm_municipalities(country, data_dir)
    municipalities_utm = municipalities_gdf.to_crs(utm_crs)
    municipalities_gdf['area_km2'] = municipalities_utm.geometry.area.div(1e6).round(2)
    # Computed once per country/run, in UTM (real meters) rather than per
    # municipality/pollutant in the loop below - see
    # pollution.estimate_own_pollution_stats()'s nearest-station fallback,
    # which needs a real-distance centroid, not a per-call reprojection.
    municipalities_gdf['centroid_utm'] = municipalities_utm.geometry.centroid

    # Population (all 5 countries, unlike the Spain-only pollution columns) -
    # zonal-summed once per country, in the GDF's native WGS84 CRS (matching
    # the WorldPop raster's CRS), same "computed in bulk before the loop"
    # shape as area_km2 above rather than per-row like the pollution columns.
    print('Fetching WorldPop population raster ...')
    population_raster_path = fetch_population_raster(GADM_CODES[country], data_dir)
    municipalities_gdf = compute_population_stats(municipalities_gdf, population_raster_path)
    municipalities_gdf['population_density'] = (
        municipalities_gdf['population'] / municipalities_gdf['area_km2']
    ).round(2)

    gadm_names = sorted(municipalities_gdf[prov_col].unique())
    config_names = {name for name, _ in province_list}
    unmatched = config_names - set(gadm_names)
    if unmatched:
        print(f'WARNING: these region names have NO GADM match: {sorted(unmatched)}')
        print(f'   GADM {prov_col} values available: {gadm_names}')
    else:
        print(f'All {len(config_names)} region name(s) matched against GADM.')

    if country_mode and not country_pbf_path.exists():
        print(f'Downloading {country_pbf_path.name} ...')
        download_pbf(COUNTRY_PBFS[country], country_pbf_path)

    if not country_mode:
        seen_urls = set()
        for region_name, url in province_list:
            if url and url not in seen_urls:
                pbf_path = data_dir / url.split('/')[-1]
                if not pbf_path.exists():
                    print(f'Downloading {pbf_path.name} ...')
                    download_pbf(url, pbf_path)
                else:
                    print(f'Cached: {pbf_path.name}')
                seen_urls.add(url)

    # Pollution columns 1-10 (Spain only for now): fetch stations, zone
    # geometries, and a year of hourly PM2.5/PM10/NO2 data ONCE per country
    # run, before the municipality loop - like the GADM/PBF downloads above,
    # not per municipality. The loop itself only does cheap in-memory
    # point-in-polygon / intersects joins + pooling against these.
    pollution_country_code = POLLUTION_COUNTRIES.get(country)
    station_gdf = None
    station_gdf_utm = None
    zone_stations_gdf = None
    zones_gdf = None
    daily_means_by_pollutant = {}
    daily_max_by_pollutant = {}
    if pollution_country_code:
        print(f'Fetching EEA station metadata + zone geometries + {pollution_year} hourly '
              f'measurements for pollution columns ...')
        station_gdf = fetch_eea_stations(pollution_country_code, data_dir)
        station_gdf_utm = station_gdf.to_crs(utm_crs)
        zones_gdf = fetch_zone_geometries(pollution_country_code, data_dir)
        zone_stations_gdf = match_stations_to_zones(station_gdf, zones_gdf)
        for pollutant in ['PM2.5', 'PM10', 'NO2']:
            hourly = fetch_eea_measurements(pollution_country_code, pollutant, pollution_year, data_dir)
            daily_means_by_pollutant[pollutant] = compute_daily_means(hourly)
            daily_max_by_pollutant[pollutant] = compute_daily_max_hourly(hourly)
            print(f'  {pollutant}: {hourly["Samplingpoint"].nunique()} sampling points, '
                  f'{len(hourly):,} hourly readings')

    checkpoint_path = f'{run_name}_checkpoint.csv'
    if os.path.exists(checkpoint_path):
        checkpoint_df = pd.read_csv(checkpoint_path)
        checkpoint_cats = set(checkpoint_df.columns) - {'Region', 'Municipality', 'GADM ID'}
        expected_cats = (
            set(CATEGORIES) | set(AMENITY_METRIC_COLUMNS) | set(POLLUTION_COLUMNS)
            | set(POPULATION_COLUMNS) | {'Municipality area (km²)'}
        )
        if checkpoint_cats != expected_cats:
            raise RuntimeError(
                f'{checkpoint_path} was written by an older/different column '
                f'schema (columns: {sorted(checkpoint_cats)}) and is not '
                f'compatible with the current schema '
                f'({sorted(expected_cats)}). Delete or rename {checkpoint_path} '
                f'(and the matching *_cycling_by_municipality.csv) and start fresh.'
            )
        done_munis = set(checkpoint_df['GADM ID'])
        municipality_results = checkpoint_df.to_dict('records')
        print(f'Resuming - {len(municipality_results)} municipalities already done.')
    else:
        done_munis = set()
        municipality_results = []
        print('Starting fresh.')

    for p_idx, (region_name, region_url) in enumerate(province_list):
        prov_munis = municipalities_gdf[
            municipalities_gdf[prov_col].str.strip() == region_name.strip()
        ].copy()
        if len(prov_munis) == 0:
            prov_munis = municipalities_gdf[
                municipalities_gdf[prov_col].str.lower().str.strip() == region_name.lower().strip()
            ].copy()
        if len(prov_munis) == 0:
            # GADM's NAME_1 values have spaces stripped out (e.g. 'LaRioja',
            # 'PaísVasco') for several regions - fall back to a
            # space-insensitive match before giving up.
            target = region_name.lower().strip().replace(' ', '')
            prov_munis = municipalities_gdf[
                municipalities_gdf[prov_col].str.lower().str.strip().str.replace(' ', '') == target
            ].copy()
        if len(prov_munis) == 0:
            print(f'[{p_idx+1}/{len(province_list)}] {region_name} - NO GADM MATCH')
            continue

        remaining = [
            row for _, row in prov_munis.iterrows()
            if row[gid_col] not in done_munis
        ]
        if not remaining:
            print(f'[{p_idx+1}/{len(province_list)}] {region_name} - already complete, skipping.')
            continue

        print(f'\n[{p_idx+1}/{len(province_list)}] {region_name} '
              f'({len(remaining)} municipalities remaining of {len(prov_munis)} total)')

        source_pbf = country_pbf_path if country_mode else data_dir / region_url.split('/')[-1]

        for m_idx, muni_row in enumerate(remaining):
            muni_name = muni_row[muni_col]
            bbox = muni_row['geometry'].bounds
            safe_name = str(muni_name).replace(' ', '_').replace('/', '_')
            temp_pbf = os.path.join(tempfile.gettempdir(), f'muni_{safe_name}.osm.pbf')

            print(f'  [{m_idx+1}/{len(remaining)}] {muni_name} ...', end=' ', flush=True)

            # Cheap in-memory joins - independent of the OSM road extraction
            # below, so they run (and are included) even if that fails.
            if pollution_country_code:
                muni_stations = match_stations_to_municipality(station_gdf, muni_row['geometry'])
                zone_ids = match_municipality_to_zones(zones_gdf, muni_row['geometry'])
                own_station_stats = estimate_own_pollution_stats(
                    muni_stations, zone_ids, zone_stations_gdf, daily_means_by_pollutant,
                    station_gdf_utm, muni_row['centroid_utm'],
                )
                zone_stats = compute_zone_pollution_stats(
                    zone_ids, zone_stations_gdf, daily_means_by_pollutant, daily_max_by_pollutant
                )
                pollution_stats = {**own_station_stats, **zone_stats}
            else:
                pollution_stats = {c: float('nan') for c in POLLUTION_COLUMNS}

            osm = roads = cycling = None
            try:
                ok = osmium_extract(str(source_pbf), bbox, temp_pbf)
                if not ok:
                    raise RuntimeError('osmium extract failed')

                osm = OSM(temp_pbf)
                roads_raw = osm.get_network(network_type='all', extra_attributes=EXTRA_OSM_ATTRIBUTES)
                if roads_raw is None or len(roads_raw) == 0:
                    raise ValueError('No roads found')
                if len(roads_raw) > max_segments:
                    raise ValueError(
                        f'{len(roads_raw):,} segments > max_segments={max_segments:,} '
                        f'(likely bbox overcount - skipping to avoid RAM crash)'
                    )

                roads = roads_raw.to_crs(utm_crs).copy()
                del roads_raw
                roads['length_m'] = roads.geometry.length

                cycling_config = get_cycling_config(country, muni_name)
                roads['category'] = roads.apply(
                    lambda r: classify_area_road(r, cycling_config), axis=1
                )
                cycling = roads[roads['category'].notna()]

                by_cat = (
                    cycling.groupby('category')['length_m']
                    .sum().div(1000)
                    .reindex(CATEGORIES, fill_value=0).round(2)
                )
                total_km = by_cat.sum()

                pois = osm.get_pois(
                    custom_filter=BIKE_AMENITY_FILTER,
                    extra_attributes=BIKE_AMENITY_EXTRA_ATTRIBUTES,
                )
                amenity_stats = compute_bike_amenity_stats(pois)

                result_row = {
                    'Region': region_name,
                    'Municipality': muni_name,
                    'GADM ID': muni_row[gid_col],
                    'Municipality area (km²)': muni_row['area_km2'],
                    'Population': muni_row['population'],
                    'Population density (per km²)': muni_row['population_density'],
                }
                result_row.update(by_cat.to_dict())
                result_row.update(amenity_stats)
                result_row.update(pollution_stats)
                municipality_results.append(result_row)
                done_munis.add(muni_row[gid_col])
                print(f'{total_km:.1f} km  ({len(cycling):,} segments)')

            except Exception as exc:
                print(f'SKIPPED - {exc}')
                result_row = {
                    'Region': region_name,
                    'Municipality': muni_name,
                    'GADM ID': muni_row[gid_col],
                    'Municipality area (km²)': muni_row['area_km2'],
                    'Population': muni_row['population'],
                    'Population density (per km²)': muni_row['population_density'],
                }
                result_row.update({c: 0.0 for c in CATEGORIES})
                result_row.update({c: 0 for c in AMENITY_METRIC_COLUMNS})
                result_row.update(pollution_stats)
                municipality_results.append(result_row)
                done_munis.add(muni_row[gid_col])

            finally:
                del osm, roads, cycling
                gc.collect()
                try:
                    os.remove(temp_pbf)
                except FileNotFoundError:
                    pass

            pd.DataFrame(municipality_results).to_csv(checkpoint_path, index=False)

        gc.collect()

    print('\nDone.')
    if not municipality_results:
        raise RuntimeError(
            'No municipalities were processed - every requested region failed '
            'to match a GADM municipality. Check the "NO GADM MATCH" warnings '
            'above against the available GADM names printed earlier.'
        )
    summary = pd.DataFrame(municipality_results)
    cat_cols = [c for c in CATEGORIES if c in summary.columns]
    amenity_cols = [c for c in AMENITY_METRIC_COLUMNS if c in summary.columns]
    pollution_cols = [c for c in POLLUTION_COLUMNS if c in summary.columns]
    summary = summary[
        ['Region', 'Municipality', 'GADM ID', 'Municipality area (km²)'] + POPULATION_COLUMNS
        + cat_cols + amenity_cols + pollution_cols
    ]

    summary.insert(6, 'Total Cycling Path km', summary[cat_cols].sum(axis=1).round(1))
    summary = summary.sort_values(['Region', 'Total Cycling Path km'], ascending=[True, False]).reset_index(drop=True)

    # GADM ID is an internal join/resume key (checkpoint resume, save_database_csv's
    # gadm_id), not something a human reader of the CSV/PNG table needs to see -
    # dropped from the human-facing outputs only; `summary` (with GADM ID intact)
    # is still what's returned and what save_database_csv() gets below.
    display_summary = summary.drop(columns=['GADM ID'])

    out_path = f'{run_name}_cycling_by_municipality.csv'
    display_summary.to_csv(out_path, index=False)
    print(f'Saved -> {out_path}')

    output_dir = Path('output')
    output_dir.mkdir(exist_ok=True)
    png_path = output_dir / f'{run_name}_cycling_by_municipality.png'
    save_table_png(display_summary, png_path, title=f'{run_name.replace("_", " ").title()} - cycling infrastructure by municipality')
    print(f'Saved -> {png_path}')

    # Separate pollution-only table (columns 1-10, pollution_columns_plan.md)
    # alongside the cycling table above - only meaningful for countries with
    # pollution data wired up (see POLLUTION_COUNTRIES); a Netherlands/etc.
    # run would otherwise render a table of nothing but blanks.
    if pollution_country_code:
        pollution_png_path = output_dir / f'{run_name}_pollution_by_municipality.png'
        pollution_table = display_summary[['Region', 'Municipality'] + pollution_cols]
        save_table_png(
            pollution_table, pollution_png_path,
            title=f'{run_name.replace("_", " ").title()} - air quality by municipality',
        )
        print(f'Saved -> {pollution_png_path}')

    # Database/webapp-ready CSV alongside the human-facing PNG/CSV above -
    # snake_case columns + a stable gadm_id + run metadata, see
    # save_database_csv()'s docstring for why this is a separate file rather
    # than just this same summary.
    db_csv_path = output_dir / f'{run_name}_municipalities.csv'
    save_database_csv(
        summary, db_csv_path, country=country, run_name=run_name,
        pollution_year=pollution_year if pollution_country_code else None,
    )
    print(f'Saved -> {db_csv_path}')

    return summary
