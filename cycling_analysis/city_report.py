"""
Stacks the per-city maps (PNG from city_map.render_map_png) into a Spanish-language
PowerPoint and PDF: title, legend, cross-city chart, comparison with the Observatorio
survey (totals, then split by type), km table, one page per city (alphabetical).
`summary` is the DataFrame scripts/city_maps.py writes (City, Region, Slug,
Total km, one column per category).
"""

import re
import textwrap
import unicodedata
from pathlib import Path

import matplotlib.image as mpimg
import numpy as np
import pandas as pd
from matplotlib.backends.backend_agg import FigureCanvasAgg
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from matplotlib.ticker import FuncFormatter
from PIL import Image
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from datetime import date

from .area_classification import CATEGORIES
from .city_map import CATEGORY_STYLE
from .observatorio_encuesta import NETWORK_CATEGORIES, survey_by_category, survey_total

TITLE = 'Infraestructura ciclista en las 13 ciudades del piloto RedBici'
SUBTITLE = 'Datos de OpenStreetMap, clasificados con la tipología de 11 categorías de OurCityInData'
SOURCE = ('Fuente: OpenStreetMap contributors (extractos de Geofabrik); límites municipales: GADM 4.1. '
          'Vías recortadas al límite de cada municipio. Las longitudes son estimaciones derivadas de OSM, no cifras oficiales.')
COMPARE_NOTE = ('Encuesta: km declarados por cada ciudad en el Observatorio de la Bicicleta (RedBici), sin validar. '
                'OSM: nuevo cálculo para las categorías que el Observatorio cuenta como red ciclable. ')

MONTHS = ['enero', 'febrero', 'marzo', 'abril', 'mayo', 'junio', 'julio', 'agosto',
          'septiembre', 'octubre', 'noviembre', 'diciembre']

CAT_ES = {
    'Greenway': 'Ciclovía / vía verde',
    'Two-way side bike lane': 'Carril bici lateral doble sentido',
    'One-way side bike lane': 'Carril bici lateral 1 sentido',
    'Bike lane on sidewalk': 'Carril bici en acera',
    'Bus-bike lane': 'Carril bus-bici',
    'Pacified zone at 10 km/h': 'Zona pacificada 10 km/h',
    'Pacified street at 20 km/h': 'Calle pacificada 20 km/h',
    'Shared street at 30 km/h': 'Ciclocalle 30 km/h',
    'Service road': 'Vía de servicio',
    '2-1 road': 'Calle 2-1',
    'Mixed traffic road': 'Tráfico mixto',
}
CAT_ES_SHORT = {
    'Greenway': 'Ciclovía', 'Two-way side bike lane': 'Lateral\n2 sentidos', 'One-way side bike lane': 'Lateral\n1 sentido',
    'Bike lane on sidewalk': 'Carril bici\nen acera', 'Bus-bike lane': 'Carril\nbus-bici',
    'Pacified zone at 10 km/h': 'Pacificada\n10 km/h', 'Pacified street at 20 km/h': 'Pacificada\n20 km/h',
    'Shared street at 30 km/h': 'Ciclocalle\n30 km/h', 'Service road': 'Vía de\nservicio', '2-1 road': 'Calle 2-1',
    'Mixed traffic road': 'Tráfico\nmixto',
}
DEFINITIONS = {
    'Greenway': 'Vías fuera de calzada, sin pavimentar o separadas (según etiquetas OSM; incluye caminos rurales)',
    'Two-way side bike lane': 'Carril o pista bici de doble sentido junto a la calzada',
    'One-way side bike lane': 'Carril o pista bici de un solo sentido junto a la calzada',
    'Bike lane on sidewalk': 'Circulación en bici permitida por la acera',
    'Bus-bike lane': 'Carril compartido con autobuses',
    'Pacified zone at 10 km/h': 'Calle de coexistencia / plataforma única, 10 km/h',
    'Pacified street at 20 km/h': 'Calle pacificada, 20 km/h',
    'Shared street at 30 km/h': 'Calle con tráfico mixto limitado a 30 km/h o menos',
    'Service road': 'Vías de servicio o acceso por las que se puede circular en bici',
    '2-1 road': 'Calzada de un carril de coche con espacio ciclista a ambos lados',
    'Mixed traffic road': 'Resto de vías donde se puede circular en bici compartiendo con el tráfico',
}
EXCLUDE_FROM_CHART = ('Mixed traffic road', 'Service road')  # dominate the scale; shown in the table
SURVEY_CATEGORIES = [c for c in CATEGORIES if c in {
    'Greenway', 'Two-way side bike lane', 'One-way side bike lane', 'Bike lane on sidewalk', 'Bus-bike lane',
    'Pacified zone at 10 km/h', 'Pacified street at 20 km/h', 'Shared street at 30 km/h'}]

# agreement colours for the by-type comparison (OSM / survey)
GOOD, OK, BAD = 'D7F0DC', 'FFF3C4', 'F9D2CF'
INK = '1C2B33'


def fmt(x, d=1):
    """Spanish number format: 1.234,5"""
    return f'{x:,.{d}f}'.replace(',', '§').replace('.', ',').replace('§', '.')


def date_es(d=None):
    d = d or date.today()
    return f'{d.day} de {MONTHS[d.month - 1]} de {d.year}'


def _sort_key(name):
    s = unicodedata.normalize('NFD', name).encode('ascii', 'ignore').decode().lower()
    return re.sub(r"[^a-z0-9 ]", '', s)


def sort_cities(summary: pd.DataFrame) -> pd.DataFrame:
    return summary.iloc[sorted(range(len(summary)), key=lambda i: _sort_key(summary['City'].iloc[i]))].reset_index(drop=True)


def network_km(row):
    """New-calculation km for the types the Observatorio counts as cyclable network."""
    return float(sum(row[c] for c in NETWORK_CATEGORIES))


def _agreement(osm, survey):
    """Fill colour for an OSM value against the declared one."""
    if survey is None:
        return None
    if abs(osm - survey) < 1:   # sub-km differences are noise, not disagreement
        return GOOD
    if survey == 0:
        return GOOD if osm < 1 else BAD
    r = osm / survey
    return GOOD if 0.75 <= r <= 1 / 0.75 else OK if 0.5 <= r <= 2 else BAD


def _city_table(row):
    total = float(row['Total km']) or 1.0
    return [(c, float(row[c]), 100 * float(row[c]) / total) for c in CATEGORIES]


# ───────────────────────────── charts (matplotlib PNG, shared by PPTX and PDF) ─────────────────────────────

def _km_axis(ax):
    ax.xaxis.set_major_formatter(FuncFormatter(lambda v, _: fmt(v, 0)))
    ax.spines[['top', 'right']].set_visible(False)
    ax.grid(axis='x', color='#e6e6e6', linewidth=0.8)
    ax.set_axisbelow(True)


def render_totals_chart(summary, out_path, dpi=220):
    """Stacked horizontal bars of dedicated / calmed infrastructure per city."""
    cats = [c for c in CATEGORIES if c not in EXCLUDE_FROM_CHART]
    fig = Figure(figsize=(12.4, 6.2), dpi=dpi, facecolor='white')
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.215, 0.20, 0.745, 0.77])
    y = np.arange(len(summary))
    left = np.zeros(len(summary))
    for c in cats:
        vals = summary[c].to_numpy(float)
        ax.barh(y, vals, left=left, color=CATEGORY_STYLE[c][0], height=0.68, label=CAT_ES[c], edgecolor='white', linewidth=0.4)
        left += vals
    for yi, tot in zip(y, left):
        ax.text(tot + left.max() * 0.008, yi, fmt(tot, 0), va='center', fontsize=10.5, fontweight='bold', color='#37474f')
    ax.set_yticks(y)
    ax.set_yticklabels(summary['City'], fontsize=12)
    ax.invert_yaxis()
    ax.set_xlim(0, left.max() * 1.07)
    ax.set_xlabel('km', fontsize=11, color='#555555')
    ax.tick_params(axis='x', labelsize=10.5)
    ax.tick_params(axis='y', length=0)
    _km_axis(ax)
    fig.legend(*ax.get_legend_handles_labels(), loc='lower center', ncol=3, frameon=False, fontsize=10.5,
               bbox_to_anchor=(0.5, 0.0), columnspacing=1.8, handlelength=1.4)
    fig.savefig(str(out_path), dpi=dpi, facecolor='white')
    return Path(out_path)


def render_total_comparison_chart(summary, out_path, dpi=220):
    """Declared (survey) total vs new OSM total per city."""
    fig = Figure(figsize=(12.4, 6.1), dpi=dpi, facecolor='white')
    FigureCanvasAgg(fig)
    ax = fig.add_axes([0.215, 0.12, 0.655, 0.84])
    y = np.arange(len(summary))
    h = 0.36
    osm = np.array([network_km(r) for _, r in summary.iterrows()])
    surv = [survey_total(c) for c in summary['City']]
    xmax = max([osm.max()] + [s for s in surv if s]) * 1.0
    c_surv, c_osm = '#7a8b99', '#00897b'
    for i, (o, s) in enumerate(zip(osm, surv)):
        ax.barh(i + h / 2, o, height=h, color=c_osm)
        ax.text(o + xmax * 0.008, i + h / 2, fmt(o, 0), va='center', fontsize=10, color='#1d5c54', fontweight='bold')
        if s is not None:
            ax.barh(i - h / 2, s, height=h, color=c_surv)
            ax.text(s + xmax * 0.008, i - h / 2, fmt(s, 0), va='center', fontsize=10, color='#4d5b66', fontweight='bold')
            ratio = o / s
            ax.text(xmax * 1.20, i, f'{fmt(ratio, 1)}×', va='center', ha='right', fontsize=11.5, fontweight='bold',
                    color='#c0392b' if ratio > 2 or ratio < 0.5 else '#37474f')
        else:
            ax.text(xmax * 0.008, i - h / 2, 'sin dato en la encuesta', va='center', fontsize=9.5, color='#999999', style='italic')
            ax.text(xmax * 1.20, i, '–', va='center', ha='right', fontsize=11.5, color='#999999')
    ax.text(xmax * 1.20, -0.95, 'OSM / encuesta', ha='right', va='center', fontsize=10.5, color='#555555', fontweight='bold')
    ax.set_yticks(y)
    ax.set_yticklabels(summary['City'], fontsize=12)
    ax.set_ylim(len(summary) - 0.4, -1.3)
    ax.set_xlim(0, xmax * 1.22)
    ax.set_xticks([t for t in ax.get_xticks() if t <= xmax * 1.02])
    ax.set_xlabel('km de red ciclable', fontsize=11, color='#555555')
    ax.tick_params(axis='y', length=0)
    _km_axis(ax)
    fig.legend([Rectangle((0, 0), 1, 1, color=c_surv), Rectangle((0, 0), 1, 1, color=c_osm)],
               ['Encuesta del Observatorio (km declarados)', 'OSM, nuevo cálculo'], loc='upper center', ncol=2,
               frameon=False, fontsize=11, bbox_to_anchor=(0.45, 1.0))
    fig.savefig(str(out_path), dpi=dpi, facecolor='white')
    return Path(out_path)


# ───────────────────────────── PPTX ─────────────────────────────

def _text(slide, x, y, w, h, text, size=14, bold=False, color=INK, align=PP_ALIGN.LEFT):
    tb = slide.shapes.add_textbox(x, y, w, h)
    tf = tb.text_frame
    tf.word_wrap = True
    p = tf.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size, r.font.bold, r.font.name = Pt(size), bold, 'Arial'
    r.font.color.rgb = RGBColor.from_string(color)
    return tb


def _rgb(hex_color):
    h = hex_color.lstrip('#')
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _cell(cell, text, size=10, bold=False, fill=None, color=INK, align=PP_ALIGN.LEFT):
    cell.text = ''
    p = cell.text_frame.paragraphs[0]
    p.alignment = align
    r = p.add_run()
    r.text = text
    r.font.size, r.font.bold, r.font.name = Pt(size), bold, 'Arial'
    r.font.color.rgb = RGBColor.from_string(color)
    cell.margin_left = cell.margin_right = Inches(0.05)
    cell.margin_top = cell.margin_bottom = Inches(0.02)
    if fill:
        cell.fill.solid()
        cell.fill.fore_color.rgb = _rgb(fill)
    else:
        cell.fill.background()


def _fit_picture(slide, path, x, y, w, h):
    iw, ih = Image.open(path).size
    scale = min(w / iw, h / ih)
    pw, ph = int(iw * scale), int(ih * scale)
    slide.shapes.add_picture(str(path), x + (w - pw) // 2, y + (h - ph) // 2, pw, ph)


def _type_cell_text(city, cat, osm):
    s = survey_by_category(city).get(cat)
    return (f'{fmt(s, 1)} / {fmt(osm, 1)}' if s is not None else f'– / {fmt(osm, 1)}'), _agreement(osm, s)


def build_pptx(summary: pd.DataFrame, map_dir: Path, out_path: Path) -> Path:
    map_dir, out_path = Path(map_dir), Path(out_path)
    summary = sort_cities(summary)
    chart_dir = map_dir / 'charts'
    chart_dir.mkdir(parents=True, exist_ok=True)
    totals_png = render_totals_chart(summary, chart_dir / 'totales_por_ciudad.png')
    compare_png = render_total_comparison_chart(summary, chart_dir / 'comparacion_total_encuesta.png')

    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]

    # 1. portada
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.8), Inches(2.3), Inches(11.7), Inches(1.6), TITLE, 38, True)
    _text(s, Inches(0.8), Inches(4.0), Inches(11.7), Inches(0.6), SUBTITLE, 18, color='555555')
    _text(s, Inches(0.8), Inches(4.7), Inches(11.7), Inches(0.5), f'OurCityInData  |  {date_es()}', 14, color='888888')

    # 2. cómo leer los mapas
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.6), Inches(0.35), Inches(12), Inches(0.7), 'Cómo leer los mapas', 28, True)
    tbl = s.shapes.add_table(len(CATEGORIES) + 1, 3, Inches(0.6), Inches(1.2), Inches(12.1), Inches(4.6)).table
    tbl.columns[0].width, tbl.columns[1].width, tbl.columns[2].width = Inches(0.5), Inches(3.4), Inches(8.2)
    for j, head in enumerate(['', 'Categoría', 'Qué incluye']):
        _cell(tbl.cell(0, j), head, 12, True, fill=INK, color='FFFFFF')
    for i, c in enumerate(CATEGORIES, start=1):
        _cell(tbl.cell(i, 0), '', fill=CATEGORY_STYLE[c][0])
        _cell(tbl.cell(i, 1), CAT_ES[c], 12, True)
        _cell(tbl.cell(i, 2), DEFINITIONS[c], 12)
    _text(s, Inches(0.6), Inches(6.2), Inches(12.1), Inches(0.9), SOURCE, 11, color='666666')

    # 3. gráfico comparativo (imagen de alta resolución)
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.6), Inches(0.25), Inches(12.2), Inches(0.7),
          'Infraestructura ciclista dedicada y pacificada por ciudad (km)', 26, True)
    _fit_picture(s, totals_png, Inches(0.45), Inches(0.95), Inches(12.4), Inches(5.75))
    _text(s, Inches(0.6), Inches(6.8), Inches(12.1), Inches(0.5),
          'No se muestran las vías de tráfico mixto ni las vías de servicio (dominan la escala); '
          'están en la tabla de km por ciudad y categoría.', 11, color='666666')

    # 4. comparación del total con la encuesta
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.6), Inches(0.25), Inches(12.2), Inches(0.7),
          'Total de km: encuesta del Observatorio vs. nuevo cálculo con OSM', 26, True)
    _fit_picture(s, compare_png, Inches(0.45), Inches(0.95), Inches(12.4), Inches(5.55))
    _text(s, Inches(0.6), Inches(6.5), Inches(12.1), Inches(0.9),
          COMPARE_NOTE + 'La red ciclable suma ciclovía, carril bici lateral (1 y 2 sentidos), carril bici en acera y zonas pacificadas '
          '10 y 20 km/h. La ciclovía de OSM incluye caminos rurales sin pavimentar, por lo que puede sobreestimar frente a la encuesta.',
          10.5, color='666666')

    # 5. comparación por tipo
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.5), Inches(0.25), Inches(12.4), Inches(0.7),
          'Desglose por tipo: encuesta del Observatorio vs. OSM (km)', 26, True)
    heads = ['Ciudad'] + [CAT_ES_SHORT[c].replace('\n', ' ') for c in SURVEY_CATEGORIES]
    tbl = s.shapes.add_table(len(summary) + 1, len(heads), Inches(0.3), Inches(1.0), Inches(12.7), Inches(5.0)).table
    tbl.columns[0].width = Inches(2.1)
    for j in range(1, len(heads)):
        tbl.columns[j].width = Emu(int((Inches(12.7) - Inches(2.1)) / (len(heads) - 1)))
    for j, head in enumerate(heads):
        fill = CATEGORY_STYLE[SURVEY_CATEGORIES[j - 1]][0] if j else INK
        _cell(tbl.cell(0, j), head, 8.5, True, fill=fill, color='FFFFFF', align=PP_ALIGN.CENTER)
    for i, (_, r) in enumerate(summary.iterrows(), start=1):
        _cell(tbl.cell(i, 0), r['City'], 10, True)
        for j, c in enumerate(SURVEY_CATEGORIES, start=1):
            text, fill = _type_cell_text(r['City'], c, float(r[c]))
            _cell(tbl.cell(i, j), text, 9, fill=fill, align=PP_ALIGN.CENTER)
    y0 = Inches(6.15)
    _text(s, Inches(0.4), y0, Inches(12.5), Inches(0.4),
          'Cada celda: km declarados en la encuesta  /  km calculados con OSM.   "–" = la ciudad no reportó ese tipo.', 11, bold=True, color='444444')
    for k, (fill, label) in enumerate([(GOOD, 'OSM entre 0,75× y 1,33× de la encuesta'),
                                       (OK, 'entre 0,5× y 2×'), (BAD, 'más de 2× o menos de 0,5×')]):
        sw = s.shapes.add_shape(1, Inches(0.45 + k * 3.7), Inches(6.62), Inches(0.3), Inches(0.22))
        sw.fill.solid()
        sw.fill.fore_color.rgb = _rgb(fill)
        sw.line.color.rgb = RGBColor(0xBB, 0xBB, 0xBB)
        _text(s, Inches(0.8 + k * 3.7), Inches(6.55), Inches(3.3), Inches(0.35), label, 10, color='555555')
    _text(s, Inches(0.4), Inches(6.95), Inches(12.5), Inches(0.5),
          'San Sebastián, Sant Cugat, Siero y Vilafranca no tienen cifras en la encuesta; Fuenlabrada y Lloret solo declararon el total. '
          'No se compara contraflujo (la nueva clasificación no lo calcula).', 9.5, color='888888')

    # 6. tabla de km
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.4), Inches(0.3), Inches(12.5), Inches(0.7), 'Kilómetros por ciudad y categoría', 26, True)
    heads = ['Ciudad', 'Total'] + [CAT_ES_SHORT[c].replace('\n', ' ') for c in CATEGORIES]
    tbl = s.shapes.add_table(len(summary) + 1, len(heads), Inches(0.3), Inches(1.0), Inches(12.7), Inches(5.6)).table
    tbl.columns[0].width = Inches(1.9)
    for j in range(1, len(heads)):
        tbl.columns[j].width = Emu(int((Inches(12.7) - Inches(1.9)) / (len(heads) - 1)))
    for j, head in enumerate(heads):
        fill = CATEGORY_STYLE[CATEGORIES[j - 2]][0] if j >= 2 else INK
        _cell(tbl.cell(0, j), head, 7.5, True, fill=fill, color='FFFFFF', align=PP_ALIGN.CENTER)
    for i, (_, r) in enumerate(summary.iterrows(), start=1):
        _cell(tbl.cell(i, 0), r['City'], 9, True)
        _cell(tbl.cell(i, 1), fmt(r['Total km'], 0), 9, True, align=PP_ALIGN.RIGHT)
        for j, c in enumerate(CATEGORIES, start=2):
            _cell(tbl.cell(i, j), fmt(r[c], 1), 9, align=PP_ALIGN.RIGHT)
    _text(s, Inches(0.4), Inches(6.8), Inches(12.5), Inches(0.5), SOURCE, 10, color='666666')

    # 7+. una diapositiva por ciudad (orden alfabético)
    for _, r in summary.iterrows():
        s = prs.slides.add_slide(blank)
        _text(s, Inches(0.5), Inches(0.25), Inches(8), Inches(0.7), r['City'], 30, True)
        _text(s, Inches(0.5), Inches(0.9), Inches(8), Inches(0.4),
              f"{r['Region']}  |  {fmt(r['Total km'], 0)} km de red apta para la bicicleta", 13, color='666666')
        _fit_picture(s, map_dir / f"{r['Slug']}_map.png", Inches(0.4), Inches(1.35), Inches(8.3), Inches(5.6))
        rows = _city_table(r)
        tbl = s.shapes.add_table(len(rows) + 1, 4, Inches(8.9), Inches(1.5), Inches(4.3), Inches(4.8)).table
        for j, w in enumerate([0.3, 2.5, 0.8, 0.7]):
            tbl.columns[j].width = Inches(w)
        for j, head in enumerate(['', 'Categoría', 'km', '%']):
            _cell(tbl.cell(0, j), head, 10, True, fill=INK, color='FFFFFF', align=PP_ALIGN.LEFT if j == 1 else PP_ALIGN.CENTER)
        for i, (c, km, pct) in enumerate(rows, start=1):
            _cell(tbl.cell(i, 0), '', fill=CATEGORY_STYLE[c][0])
            _cell(tbl.cell(i, 1), CAT_ES[c], 9.5)
            _cell(tbl.cell(i, 2), fmt(km, 1), 9.5, align=PP_ALIGN.RIGHT)
            _cell(tbl.cell(i, 3), f'{pct:.0f}%', 9.5, align=PP_ALIGN.RIGHT)
        _text(s, Inches(0.5), Inches(7.0), Inches(12.3), Inches(0.4), SOURCE, 8.5, color='888888')

    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


# ───────────────────────────── PDF ─────────────────────────────

def _page():
    return Figure(figsize=(13.333, 7.5), facecolor='white')


def _pdf_table(fig, x, y, w, rows, row_h=0.047, font=9.5):
    fig.text(x + 0.03, y, 'Categoría', fontsize=font, fontweight='bold', va='center')
    fig.text(x + w - 0.065, y, 'km', fontsize=font, fontweight='bold', va='center', ha='right')
    fig.text(x + w, y, '%', fontsize=font, fontweight='bold', va='center', ha='right')
    for i, (c, km, pct) in enumerate(rows, start=1):
        yy = y - i * row_h
        fig.add_artist(Rectangle((x, yy - 0.012), 0.016, 0.024, color=CATEGORY_STYLE[c][0], transform=fig.transFigure))
        fig.text(x + 0.03, yy, CAT_ES[c], fontsize=font, va='center')
        fig.text(x + w - 0.065, yy, fmt(km, 1), fontsize=font, va='center', ha='right')
        fig.text(x + w, yy, f'{pct:.0f}%', fontsize=font, va='center', ha='right')


def _style_table(tab, n_cols, header_fills, first_col_left=True, header_h=1.6):
    tab.auto_set_font_size(False)
    for (i, j), cell in tab.get_celld().items():
        cell.set_edgecolor('#dddddd')
        if j == 0 and i > 0 and first_col_left:
            cell._loc = 'left'
            cell.set_text_props(ha='left')
        if i == 0:
            cell.set_facecolor('#' + header_fills[j])
            cell.set_text_props(color='white', fontweight='bold', ha='center')
            cell.set_height(cell.get_height() * header_h)


def build_pdf(summary: pd.DataFrame, map_dir: Path, out_path: Path) -> Path:
    map_dir, out_path = Path(map_dir), Path(out_path)
    summary = sort_cities(summary)
    chart_dir = map_dir / 'charts'
    chart_dir.mkdir(parents=True, exist_ok=True)
    totals_png = render_totals_chart(summary, chart_dir / 'totales_por_ciudad.png')
    compare_png = render_total_comparison_chart(summary, chart_dir / 'comparacion_total_encuesta.png')
    out_path.parent.mkdir(parents=True, exist_ok=True)
    foot = dict(fontsize=7.5, color='#888888')
    head = dict(fontweight='bold', color='#' + INK)

    def image_page(title, png, note=None, note_size=8.5):
        f = _page()
        f.text(0.04, 0.94, title, fontsize=21, **head)
        ax = f.add_axes([0.02, 0.10 if note else 0.04, 0.96, 0.80])
        ax.imshow(mpimg.imread(str(png)))
        ax.set_axis_off()
        if note:
            f.text(0.04, 0.075, textwrap.fill(note, 175), fontsize=note_size, color='#666666', va='center')
        return f

    with PdfPages(str(out_path)) as pdf:
        f = _page()
        f.text(0.06, 0.58, 'Infraestructura ciclista en las\n13 ciudades del piloto RedBici', fontsize=34, va='center', **head)
        f.text(0.06, 0.38, SUBTITLE, fontsize=15, color='#555555')
        f.text(0.06, 0.31, f'OurCityInData  |  {date_es()}', fontsize=12, color='#888888')
        pdf.savefig(f)

        f = _page()
        f.text(0.05, 0.93, 'Cómo leer los mapas', fontsize=24, **head)
        for i, c in enumerate(CATEGORIES):
            yy = 0.82 - i * 0.058
            f.add_artist(Rectangle((0.05, yy - 0.014), 0.025, 0.028, color=CATEGORY_STYLE[c][0], transform=f.transFigure))
            f.text(0.09, yy, CAT_ES[c], fontsize=12, fontweight='bold', va='center')
            f.text(0.33, yy, DEFINITIONS[c], fontsize=10.5, va='center', color='#333333')
        f.text(0.05, 0.1, SOURCE, **foot)
        pdf.savefig(f)

        pdf.savefig(image_page('Infraestructura ciclista dedicada y pacificada por ciudad (km)', totals_png,
                               'No se muestran las vías de tráfico mixto ni las vías de servicio (dominan la escala); están en la tabla de km por ciudad y categoría.', 9))

        pdf.savefig(image_page(
            'Total de km: encuesta del Observatorio vs. nuevo cálculo con OSM', compare_png,
            COMPARE_NOTE + 'La red ciclable suma ciclovía, carril bici lateral (1 y 2 sentidos), carril bici en acera y zonas pacificadas 10 y 20 km/h. '
            'La ciclovía de OSM incluye caminos rurales sin pavimentar, por lo que puede sobreestimar frente a la encuesta.', 8))

        # desglose por tipo
        f = _page()
        f.text(0.03, 0.94, 'Desglose por tipo: encuesta del Observatorio vs. OSM (km)', fontsize=21, **head)
        ax = f.add_axes([0.02, 0.19, 0.96, 0.70])
        ax.set_axis_off()
        cols = ['Ciudad'] + [CAT_ES_SHORT[c] for c in SURVEY_CATEGORIES]
        text, colors = [], []
        for _, r in summary.iterrows():
            row_t, row_c = [r['City']], ['white']
            for c in SURVEY_CATEGORIES:
                t, fill = _type_cell_text(r['City'], c, float(r[c]))
                row_t.append(t)
                row_c.append('#' + fill if fill else 'white')
            text.append(row_t)
            colors.append(row_c)
        tab = ax.table(cellText=text, colLabels=cols, cellColours=colors, loc='upper center', cellLoc='center')
        tab.set_fontsize(9)
        tab.scale(1, 2.0)
        _style_table(tab, len(cols), [INK] + [CATEGORY_STYLE[c][0].lstrip('#') for c in SURVEY_CATEGORIES])
        tab.auto_set_column_width(range(len(cols)))
        f.text(0.03, 0.135, 'Cada celda: km declarados en la encuesta  /  km calculados con OSM.   "–" = la ciudad no reportó ese tipo.',
               fontsize=10, fontweight='bold', color='#444444')
        for k, (fill, label) in enumerate([(GOOD, 'OSM entre 0,75× y 1,33× de la encuesta'), (OK, 'entre 0,5× y 2×'),
                                           (BAD, 'más de 2× o menos de 0,5×')]):
            f.add_artist(Rectangle((0.03 + k * 0.28, 0.092), 0.018, 0.026, color='#' + fill, transform=f.transFigure))
            f.text(0.055 + k * 0.28, 0.105, label, fontsize=9, va='center', color='#555555')
        f.text(0.03, 0.045, 'San Sebastián, Sant Cugat, Siero y Vilafranca no tienen cifras en la encuesta; Fuenlabrada y Lloret solo declararon '
               'el total. No se compara contraflujo (la nueva clasificación no lo calcula).', fontsize=8, color='#888888')
        pdf.savefig(f)

        # tabla de km
        f = _page()
        f.text(0.03, 0.94, 'Kilómetros por ciudad y categoría', fontsize=21, **head)
        ax = f.add_axes([0.02, 0.10, 0.96, 0.80])
        ax.set_axis_off()
        cols = ['Ciudad', 'Total'] + [CAT_ES_SHORT[c] for c in CATEGORIES]
        text = [[r['City'], fmt(r['Total km'], 0)] + [fmt(r[c], 1) for c in CATEGORIES] for _, r in summary.iterrows()]
        tab = ax.table(cellText=text, colLabels=cols, loc='upper center', cellLoc='right')
        tab.set_fontsize(8.5)
        tab.scale(1, 2.55)
        _style_table(tab, len(cols), [INK, INK] + [CATEGORY_STYLE[c][0].lstrip('#') for c in CATEGORIES])
        tab.auto_set_column_width(range(len(cols)))
        f.text(0.03, 0.04, SOURCE, **foot)
        pdf.savefig(f)

        # una página por ciudad
        for _, r in summary.iterrows():
            f = _page()
            f.text(0.04, 0.945, r['City'], fontsize=26, **head)
            f.text(0.04, 0.895, f"{r['Region']}  |  {fmt(r['Total km'], 0)} km de red apta para la bicicleta", fontsize=12, color='#666666')
            ax = f.add_axes([0.02, 0.07, 0.62, 0.80])
            ax.imshow(mpimg.imread(str(map_dir / f"{r['Slug']}_map.png")))
            ax.set_axis_off()
            _pdf_table(f, 0.67, 0.80, 0.30, _city_table(r))
            f.text(0.04, 0.03, SOURCE, **foot)
            pdf.savefig(f)
    return out_path
