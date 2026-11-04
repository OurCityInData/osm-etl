"""
Single-city cycling-infrastructure maps (interactive HTML + static PNG), using
the same per-municipality pipeline as country.py's run_country_analysis():
bbox-extract from the cached regional PBF, trim roads to the real GADM
boundary (clip_roads_to_polygon), classify with classify_area_road().

The interactive maps use key-free basemaps only. CARTO's free `light_all`
tiles (what the earlier notebook maps used) now answer with "API KEY REQUIRED"
placeholder tiles, which is the watermark that showed over every old map.
"""

import html
import os
import tempfile
from pathlib import Path

import rasterio  # noqa: F401  (must be imported before geopandas - see country.py)

import folium
import geopandas as gpd
import pandas as pd
import shapely
from folium.features import GeoJsonTooltip
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.figure import Figure
from matplotlib.lines import Line2D
from pyrosm import OSM

from .area_classification import CATEGORIES, EXTRA_OSM_ATTRIBUTES, classify_area_road, get_cycling_config
from .country import (
    GADM_CONFIG,
    REGIONS,
    UTM_CRS_BY_COUNTRY,
    ascii_slug,
    clip_roads_to_polygon,
    load_gadm_municipalities,
    osmium_extract,
)

# category -> (colour, line weight px, opacity)
CATEGORY_STYLE = {
    'Greenway':                   ('#00897b', 3.0, 0.95),
    'Two-way side bike lane':     ('#3fa9f5', 3.0, 0.95),
    'One-way side bike lane':     ('#1f5fbf', 3.0, 0.95),
    'Bike lane on sidewalk':      ('#ff9800', 3.0, 0.95),
    'Bus-bike lane':              ('#9c27b0', 3.0, 0.95),
    'Pacified zone at 10 km/h':   ('#ff7043', 2.5, 0.95),
    'Pacified street at 20 km/h': ('#fdd835', 2.5, 0.95),
    'Shared street at 30 km/h':   ('#4caf50', 2.5, 0.95),
    'Service road':               ('#8d6e63', 1.5, 0.75),
    '2-1 road':                   ('#ec407a', 3.0, 0.95),
    'Mixed traffic road':         ('#b0b7bd', 1.2, 0.85),
}
assert set(CATEGORY_STYLE) == set(CATEGORIES), 'CATEGORY_STYLE out of sync with CATEGORIES'

# Drawn first -> last, so the dense low-priority network sits underneath.
DRAW_ORDER = [
    'Mixed traffic road', 'Service road', 'Shared street at 30 km/h',
    'Pacified street at 20 km/h', 'Pacified zone at 10 km/h', 'Bus-bike lane',
    '2-1 road', 'Bike lane on sidewalk', 'Greenway', 'Two-way side bike lane',
    'One-way side bike lane',
]

BASEMAPS = [  # (name, url, attribution, max_native_zoom) - none needs an API key
    ('Light grey (Esri)',
     'https://server.arcgisonline.com/ArcGIS/rest/services/Canvas/World_Light_Gray_Base/MapServer/tile/{z}/{y}/{x}',
     'Tiles &copy; Esri &mdash; Esri, HERE, Garmin, &copy; OpenStreetMap contributors', 16),
    ('OpenStreetMap',
     'https://tile.openstreetmap.org/{z}/{x}/{y}.png',
     '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors', 19),
    ('CyclOSM',
     'https://{s}.tile-cyclosm.openstreetmap.fr/cyclosm/{z}/{x}/{y}.png',
     '<a href="https://www.cyclosm.org">CyclOSM</a> | Map data &copy; OpenStreetMap contributors', 20),
]


def region_pbf(country: str, region_name: str, data_dir: Path) -> Path:
    """Cached regional PBF for a REGIONS[country] entry."""
    for name, url in REGIONS[country]:
        if name == region_name:
            return Path(data_dir) / url.split('/')[-1]
    raise KeyError(f'{region_name!r} is not in REGIONS[{country!r}]')


def load_city_roads(country: str, gid: str, region_name: str, data_dir: Path = Path('data')):
    """
    Returns (cycling, polygon_utm, muni_name): classified roads (UTM CRS, only
    segments with a category, clipped to the municipal boundary, with length_m)
    plus the boundary polygon in the same CRS.
    """
    gadm = load_gadm_municipalities(country, Path(data_dir))
    gid_col = f"GID_{GADM_CONFIG[country]['level']}"
    muni_col = GADM_CONFIG[country]['muni_col']
    rows = gadm[gadm[gid_col] == gid]
    if rows.empty:
        raise KeyError(f'{gid} not found in GADM {country}')
    row = rows.iloc[0]
    muni_name = row[muni_col]

    utm_crs = UTM_CRS_BY_COUNTRY[country]
    poly_utm = gpd.GeoSeries([row.geometry], crs=gadm.crs).to_crs(utm_crs).iloc[0]

    source_pbf = region_pbf(country, region_name, Path(data_dir))
    if not source_pbf.exists():
        raise FileNotFoundError(f'{source_pbf} missing - run country_analysis.py once or download it')

    temp_pbf = os.path.join(tempfile.gettempdir(), f'citymap_{ascii_slug(muni_name)}_{gid}.osm.pbf')
    try:
        if not osmium_extract(str(source_pbf), row.geometry.bounds, temp_pbf):
            raise RuntimeError('osmium extract failed')
        roads = OSM(temp_pbf).get_network(network_type='all', extra_attributes=EXTRA_OSM_ATTRIBUTES)
        if roads is None or len(roads) == 0:
            raise ValueError('no roads found')
        roads = roads.to_crs(utm_crs).copy()
        roads = clip_roads_to_polygon(roads, poly_utm)
        if len(roads) == 0:
            raise ValueError('no roads inside the municipal boundary')
        cfg = get_cycling_config(country, muni_name)
        roads['category'] = roads.apply(lambda r: classify_area_road(r, cfg), axis=1)
    finally:
        try:
            os.remove(temp_pbf)
        except FileNotFoundError:
            pass

    cycling = roads[roads['category'].notna()].copy()
    cycling['length_m'] = cycling.geometry.length
    keep = [c for c in ('name', 'highway', 'category', 'length_m') if c in cycling.columns]
    return cycling[keep + ['geometry']], poly_utm, muni_name


def km_by_category(cycling: gpd.GeoDataFrame) -> pd.Series:
    km = cycling.groupby('category')['length_m'].sum().div(1000)
    return km.reindex(CATEGORIES).fillna(0.0)


def _web_layer(gdf: gpd.GeoDataFrame) -> gpd.GeoDataFrame:
    """WGS84 copy with 1e-6 deg (~0.1 m) coordinate precision, to keep the HTML small."""
    out = gdf.to_crs('EPSG:4326').copy()
    out['geometry'] = shapely.set_precision(out.geometry.values, 1e-6)
    out = out[~out.geometry.is_empty & out.geometry.notna()]
    for col in ('name', 'highway'):
        out[col] = out[col].fillna('').astype(str) if col in out.columns else ''
    out['length_m'] = out['length_m'].round(0).astype(int)
    return out[['name', 'highway', 'category', 'length_m', 'geometry']]


def _legend_html(title: str, km: pd.Series) -> str:
    total = float(km.sum())
    items = ''.join(
        f'<div style="display:flex;align-items:center;margin:2px 0;">'
        f'<span style="display:inline-block;width:22px;height:0;border-top:{max(2, int(CATEGORY_STYLE[c][1]))}px solid '
        f'{CATEGORY_STYLE[c][0]};margin-right:8px;"></span>'
        f'<span style="flex:1">{html.escape(c)}</span>'
        f'<span style="margin-left:14px;color:#555;font-variant-numeric:tabular-nums">{km[c]:,.1f} km</span></div>'
        for c in CATEGORIES
    )
    return (
        '<div style="position:fixed;bottom:24px;left:12px;z-index:9999;background:rgba(255,255,255,.94);'
        'padding:10px 14px;border-radius:8px;box-shadow:0 1px 6px rgba(0,0,0,.3);'
        'font:12px/1.35 Arial,Helvetica,sans-serif;color:#222;min-width:290px;">'
        f'<div style="font-weight:bold;font-size:14px;margin-bottom:4px;">{html.escape(title)}</div>'
        f'<div style="color:#555;margin-bottom:6px;">Cycling infrastructure by category &middot; {total:,.1f} km</div>'
        f'{items}'
        '<div style="margin-top:6px;color:#888;font-size:10px;">Source: OpenStreetMap, classified by OurCityInData</div></div>'
    )


def build_html_map(cycling: gpd.GeoDataFrame, poly_utm, title: str, out_path: Path) -> Path:
    km = km_by_category(cycling)
    boundary = gpd.GeoSeries([poly_utm], crs=cycling.crs).to_crs('EPSG:4326')
    minx, miny, maxx, maxy = boundary.total_bounds

    m = folium.Map(location=[(miny + maxy) / 2, (minx + maxx) / 2], tiles=None, zoom_start=12,
                   control_scale=True, prefer_canvas=True)
    for i, (name, url, attr, native) in enumerate(BASEMAPS):
        folium.TileLayer(tiles=url, attr=attr, name=name, overlay=False, control=True,
                         show=(i == 0), max_native_zoom=native, max_zoom=19).add_to(m)

    folium.GeoJson(
        boundary.__geo_interface__, name='Municipal boundary', control=True,
        style_function=lambda _f: {'color': '#37474f', 'weight': 2, 'dashArray': '6 5', 'fillOpacity': 0.0},
    ).add_to(m)

    layer = _web_layer(cycling)
    for cat in DRAW_ORDER:
        sub = layer[layer['category'] == cat]
        if sub.empty:
            continue
        color, weight, opacity = CATEGORY_STYLE[cat]
        group = folium.FeatureGroup(name=f'{cat} ({km[cat]:,.1f} km)', show=True)
        folium.GeoJson(
            sub.__geo_interface__,
            style_function=lambda _f, c=color, w=weight, o=opacity: {'color': c, 'weight': w, 'opacity': o},
            highlight_function=lambda _f: {'weight': 6, 'opacity': 1},
            tooltip=GeoJsonTooltip(fields=['category', 'name', 'highway', 'length_m'],
                                   aliases=['Category:', 'Street:', 'OSM highway:', 'Length (m):']),
        ).add_to(group)
        group.add_to(m)

    wminx, wminy, wmaxx, wmaxy = layer.total_bounds
    m.fit_bounds([[wminy, wminx], [wmaxy, wmaxx]], padding=(20, 20))
    m.get_root().html.add_child(folium.Element(_legend_html(title, km)))
    folium.LayerControl(collapsed=True, position='topright').add_to(m)
    m.get_root().header.add_child(folium.Element(f'<title>{html.escape(title)} - cycling infrastructure</title>'))

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    m.save(str(out_path))
    return out_path


def render_map_png(cycling: gpd.GeoDataFrame, poly_utm, out_path: Path, title: str = None,
                   with_legend: bool = False, dpi: int = 220) -> Path:
    """Static map in the metric CRS (equal aspect). Uses Figure/FigureCanvasAgg like country.save_table_png."""
    km = km_by_category(cycling)
    fig = Figure(figsize=(11, 8.2) if with_legend else (10, 7.6), dpi=dpi, facecolor='white')
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.02, 0.02, 0.62 if with_legend else 0.96, 0.90 if title else 0.96])

    boundary = gpd.GeoSeries([poly_utm], crs=cycling.crs)
    boundary.plot(ax=ax, facecolor='#f6f6f2', edgecolor='#37474f', linewidth=1.2, linestyle='--', zorder=0)

    for cat in DRAW_ORDER:
        sub = cycling[cycling['category'] == cat]
        if sub.empty:
            continue
        color, weight, opacity = CATEGORY_STYLE[cat]
        sub.plot(ax=ax, color=color, linewidth=weight * 0.45, alpha=opacity, zorder=2 + DRAW_ORDER.index(cat))

    bx0, by0, bx1, by1 = cycling.total_bounds
    mx, my = (bx1 - bx0) * 0.03, (by1 - by0) * 0.03
    ax.set_xlim(bx0 - mx, bx1 + mx)
    ax.set_ylim(by0 - my, by1 + my)
    ax.set_aspect('equal')
    ax.set_axis_off()
    if title:
        fig.text(0.02, 0.965, title, fontsize=17, fontweight='bold', va='center', color='#1c2b33')
    if with_legend:
        handles = [Line2D([0], [0], color=CATEGORY_STYLE[c][0], lw=3.2, label=f'{c}   {km[c]:,.1f} km') for c in CATEGORIES]
        fig.legend(handles=handles, loc='center right', bbox_to_anchor=(0.99, 0.5), frameon=False, fontsize=9.5,
                   title=f'Cycling infrastructure - {km.sum():,.1f} km', title_fontsize=10.5, alignment='left')

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(str(out_path), dpi=dpi, facecolor='white')
    return out_path
