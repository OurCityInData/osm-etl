"""
Stacks the per-city maps (PNG from city_map.render_map_png) into a PowerPoint
and a PDF: title, legend/method, cross-city comparison, one page per city.
`summary` is the DataFrame scripts/city_maps.py writes (City, Region, Slug,
Total km, one column per category).
"""

from datetime import date
from pathlib import Path

import matplotlib.image as mpimg
import pandas as pd
from matplotlib.backends.backend_pdf import PdfPages
from matplotlib.figure import Figure
from matplotlib.patches import Rectangle
from PIL import Image
from pptx import Presentation
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION
from pptx.enum.text import PP_ALIGN
from pptx.util import Emu, Inches, Pt

from .area_classification import CATEGORIES
from .city_map import CATEGORY_STYLE

SUBTITLE = 'OpenStreetMap data, classified with the OurCityInData 11-category taxonomy'
SOURCE = ('Source: OpenStreetMap contributors (Geofabrik extracts); municipal boundaries: GADM 4.1. '
          'Roads clipped to each municipality. Lengths are OSM-derived estimates, not official figures.')
DEFINITIONS = {
    'Greenway': 'Off-road / unpaved / separated paths (tag-based, includes rural tracks)',
    'Two-way side bike lane': 'Two-way cycle track beside the road',
    'One-way side bike lane': 'One-way cycle lane or track beside the road',
    'Bike lane on sidewalk': 'Cycling allowed on the sidewalk',
    'Bus-bike lane': 'Lane shared with buses',
    'Pacified zone at 10 km/h': 'Shared street / living street, 10 km/h',
    'Pacified street at 20 km/h': 'Traffic-calmed street, 20 km/h',
    'Shared street at 30 km/h': 'Mixed traffic street, 30 km/h or less',
    'Service road': 'Service / access roads usable by bike',
    '2-1 road': 'Two-lane road with a central cycling space',
    'Mixed traffic road': 'Other roads where cycling is legal, sharing with traffic',
}
EXCLUDE_FROM_CHART = ('Mixed traffic road', 'Service road')  # dominate the scale; shown in the table


def _rgb(hex_color):
    h = hex_color.lstrip('#')
    return RGBColor(int(h[0:2], 16), int(h[2:4], 16), int(h[4:6], 16))


def _city_table(row):
    total = float(row['Total km']) or 1.0
    return [(c, float(row[c]), 100 * float(row[c]) / total) for c in CATEGORIES]


# ───────────────────────────── PPTX ─────────────────────────────

def _text(slide, x, y, w, h, text, size=14, bold=False, color='1C2B33', align=PP_ALIGN.LEFT):
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


def _cell(cell, text, size=10, bold=False, fill=None, color='1C2B33', align=PP_ALIGN.LEFT):
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


def build_pptx(summary: pd.DataFrame, map_dir: Path, out_path: Path) -> Path:
    map_dir = Path(map_dir)
    prs = Presentation()
    prs.slide_width, prs.slide_height = Inches(13.333), Inches(7.5)
    blank = prs.slide_layouts[6]
    W, H = prs.slide_width, prs.slide_height

    # 1. title
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.8), Inches(2.3), Inches(11.7), Inches(1.6),
          'Cycling infrastructure in the 13 RedBici pilot cities', 38, True)
    _text(s, Inches(0.8), Inches(4.0), Inches(11.7), Inches(0.6), SUBTITLE, 18, color='555555')
    _text(s, Inches(0.8), Inches(4.7), Inches(11.7), Inches(0.5),
          f'OurCityInData  |  {date.today():%d %B %Y}', 14, color='888888')

    # 2. legend + method
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.6), Inches(0.35), Inches(12), Inches(0.7), 'How to read the maps', 28, True)
    tbl = s.shapes.add_table(len(CATEGORIES) + 1, 3, Inches(0.6), Inches(1.2), Inches(12.1), Inches(4.6)).table
    tbl.columns[0].width, tbl.columns[1].width, tbl.columns[2].width = Inches(0.5), Inches(3.4), Inches(8.2)
    for j, head in enumerate(['', 'Category', 'What it covers']):
        _cell(tbl.cell(0, j), head, 12, True, fill='1C2B33', color='FFFFFF')
    for i, c in enumerate(CATEGORIES, start=1):
        _cell(tbl.cell(i, 0), '', fill=CATEGORY_STYLE[c][0])
        _cell(tbl.cell(i, 1), c, 12, True)
        _cell(tbl.cell(i, 2), DEFINITIONS[c], 12)
    _text(s, Inches(0.6), Inches(6.2), Inches(12.1), Inches(0.9), SOURCE, 11, color='666666')

    # 3. comparison chart (dedicated infrastructure only)
    cats = [c for c in CATEGORIES if c not in EXCLUDE_FROM_CHART]
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.6), Inches(0.3), Inches(12), Inches(0.7),
          'Dedicated and calmed cycling infrastructure by city (km)', 26, True)
    cd = CategoryChartData()
    cd.categories = list(summary['City'])
    for c in cats:
        cd.add_series(c, [round(float(v), 1) for v in summary[c]])
    gf = s.shapes.add_chart(XL_CHART_TYPE.BAR_STACKED, Inches(0.5), Inches(1.0), Inches(12.3), Inches(5.7), cd)
    chart = gf.chart
    chart.has_legend, chart.legend.position, chart.legend.include_in_layout = True, XL_LEGEND_POSITION.BOTTOM, False
    chart.legend.font.size = Pt(10)
    chart.category_axis.reverse_order = True
    chart.category_axis.tick_labels.font.size = Pt(11)
    chart.value_axis.tick_labels.font.size = Pt(10)
    for ser, c in zip(chart.plots[0].series, cats):
        ser.format.fill.solid()
        ser.format.fill.fore_color.rgb = _rgb(CATEGORY_STYLE[c][0])
    _text(s, Inches(0.6), Inches(6.8), Inches(12.1), Inches(0.5),
          'Mixed traffic roads and service roads are left out of this chart (they dominate the scale); see the next table.',
          11, color='666666')

    # 4. all-category table
    s = prs.slides.add_slide(blank)
    _text(s, Inches(0.4), Inches(0.3), Inches(12.5), Inches(0.7), 'Kilometres by city and category', 26, True)
    heads = ['City', 'Total'] + CATEGORIES
    tbl = s.shapes.add_table(len(summary) + 1, len(heads), Inches(0.3), Inches(1.0), Inches(12.7), Inches(5.6)).table
    tbl.columns[0].width = Inches(1.9)
    for j in range(1, len(heads)):
        tbl.columns[j].width = Emu(int((Inches(12.7) - Inches(1.9)) / (len(heads) - 1)))
    for j, head in enumerate(heads):
        fill = CATEGORY_STYLE[head][0] if head in CATEGORY_STYLE else '1C2B33'
        _cell(tbl.cell(0, j), head, 7.5, True, fill=fill, color='FFFFFF', align=PP_ALIGN.CENTER)
    for i, (_, r) in enumerate(summary.iterrows(), start=1):
        _cell(tbl.cell(i, 0), r['City'], 9, True)
        _cell(tbl.cell(i, 1), f"{r['Total km']:,.0f}", 9, True, align=PP_ALIGN.RIGHT)
        for j, c in enumerate(CATEGORIES, start=2):
            _cell(tbl.cell(i, j), f'{r[c]:,.1f}', 9, align=PP_ALIGN.RIGHT)
    _text(s, Inches(0.4), Inches(6.8), Inches(12.5), Inches(0.5), SOURCE, 10, color='666666')

    # 5+. one slide per city
    for _, r in summary.iterrows():
        s = prs.slides.add_slide(blank)
        _text(s, Inches(0.5), Inches(0.25), Inches(8), Inches(0.7), r['City'], 30, True)
        _text(s, Inches(0.5), Inches(0.9), Inches(8), Inches(0.4), f"{r['Region']}  |  {r['Total km']:,.0f} km of cycling-legal network",
              13, color='666666')
        _fit_picture(s, map_dir / f"{r['Slug']}_map.png", Inches(0.4), Inches(1.35), Inches(8.3), Inches(5.6))
        rows = _city_table(r)
        tbl = s.shapes.add_table(len(rows) + 1, 4, Inches(8.9), Inches(1.5), Inches(4.2), Inches(4.8)).table
        for j, w in enumerate([0.3, 2.3, 0.9, 0.7]):
            tbl.columns[j].width = Inches(w)
        for j, head in enumerate(['', 'Category', 'km', '%']):
            _cell(tbl.cell(0, j), head, 10, True, fill='1C2B33', color='FFFFFF', align=PP_ALIGN.CENTER if j != 1 else PP_ALIGN.LEFT)
        for i, (c, km, pct) in enumerate(rows, start=1):
            _cell(tbl.cell(i, 0), '', fill=CATEGORY_STYLE[c][0])
            _cell(tbl.cell(i, 1), c, 10)
            _cell(tbl.cell(i, 2), f'{km:,.1f}', 10, align=PP_ALIGN.RIGHT)
            _cell(tbl.cell(i, 3), f'{pct:.0f}%', 10, align=PP_ALIGN.RIGHT)
        _text(s, Inches(0.5), Inches(7.0), Inches(12.3), Inches(0.4), SOURCE, 8.5, color='888888')

    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    prs.save(str(out_path))
    return out_path


# ───────────────────────────── PDF ─────────────────────────────

def _page():
    fig = Figure(figsize=(13.333, 7.5), facecolor='white')
    return fig


def _pdf_table(fig, x, y, w, rows, row_h=0.047, font=9.5):
    """Draw a swatch/category/km/% table in figure coordinates (top-left at x, y)."""
    fig.text(x + 0.03, y, 'Category', fontsize=font, fontweight='bold', va='center')
    fig.text(x + w - 0.065, y, 'km', fontsize=font, fontweight='bold', va='center', ha='right')
    fig.text(x + w, y, '%', fontsize=font, fontweight='bold', va='center', ha='right')
    for i, (c, km, pct) in enumerate(rows, start=1):
        yy = y - i * row_h
        fig.add_artist(Rectangle((x, yy - 0.012), 0.016, 0.024, color=CATEGORY_STYLE[c][0], transform=fig.transFigure))
        fig.text(x + 0.03, yy, c, fontsize=font, va='center')
        fig.text(x + w - 0.065, yy, f'{km:,.1f}', fontsize=font, va='center', ha='right')
        fig.text(x + w, yy, f'{pct:.0f}%', fontsize=font, va='center', ha='right')


def build_pdf(summary: pd.DataFrame, map_dir: Path, out_path: Path) -> Path:
    map_dir = Path(map_dir)
    out_path = Path(out_path)
    out_path.parent.mkdir(parents=True, exist_ok=True)
    foot = dict(fontsize=7.5, color='#888888')

    with PdfPages(str(out_path)) as pdf:
        # title
        f = _page()
        f.text(0.06, 0.58, 'Cycling infrastructure in the\n13 RedBici pilot cities', fontsize=34, fontweight='bold', va='center', color='#1C2B33')
        f.text(0.06, 0.38, SUBTITLE, fontsize=15, color='#555555')
        f.text(0.06, 0.31, f'OurCityInData  |  {date.today():%d %B %Y}', fontsize=12, color='#888888')
        pdf.savefig(f)

        # legend + method
        f = _page()
        f.text(0.05, 0.93, 'How to read the maps', fontsize=24, fontweight='bold', color='#1C2B33')
        for i, c in enumerate(CATEGORIES):
            yy = 0.82 - i * 0.058
            f.add_artist(Rectangle((0.05, yy - 0.014), 0.025, 0.028, color=CATEGORY_STYLE[c][0], transform=f.transFigure))
            f.text(0.09, yy, c, fontsize=12, fontweight='bold', va='center')
            f.text(0.31, yy, DEFINITIONS[c], fontsize=11.5, va='center', color='#333333')
        f.text(0.05, 0.1, SOURCE, **foot)
        pdf.savefig(f)

        # comparison chart
        cats = [c for c in CATEGORIES if c not in EXCLUDE_FROM_CHART]
        f = _page()
        f.text(0.05, 0.94, 'Dedicated and calmed cycling infrastructure by city (km)', fontsize=21, fontweight='bold', color='#1C2B33')
        ax = f.add_axes([0.16, 0.14, 0.80, 0.74])
        left = pd.Series(0.0, index=summary.index)
        for c in cats:
            ax.barh(summary['City'], summary[c], left=left, color=CATEGORY_STYLE[c][0], label=c, height=0.7)
            left += summary[c]
        ax.invert_yaxis()
        ax.spines[['top', 'right']].set_visible(False)
        ax.tick_params(labelsize=10)
        ax.legend(ncol=3, fontsize=8.5, frameon=False, loc='upper center', bbox_to_anchor=(0.5, -0.05))
        f.text(0.05, 0.03, 'Mixed traffic roads and service roads are left out of this chart (they dominate the scale); see the next page.', **foot)
        pdf.savefig(f)

        # all-category table
        f = _page()
        f.text(0.03, 0.94, 'Kilometres by city and category', fontsize=21, fontweight='bold', color='#1C2B33')
        ax = f.add_axes([0.02, 0.10, 0.96, 0.80])
        ax.set_axis_off()
        short = {'Two-way side bike lane': 'Two-way\nside lane', 'One-way side bike lane': 'One-way\nside lane',
                 'Bike lane on sidewalk': 'Lane on\nsidewalk', 'Pacified zone at 10 km/h': 'Pacified\n10 km/h',
                 'Pacified street at 20 km/h': 'Pacified\n20 km/h', 'Shared street at 30 km/h': 'Shared\n30 km/h',
                 'Mixed traffic road': 'Mixed\ntraffic', 'Service road': 'Service\nroad', 'Bus-bike lane': 'Bus-bike\nlane'}
        cols = ['City', 'Total'] + [short.get(c, c) for c in CATEGORIES]
        cell_text = [[r['City'], f"{r['Total km']:,.0f}"] + [f'{r[c]:,.1f}' for c in CATEGORIES] for _, r in summary.iterrows()]
        tab = ax.table(cellText=cell_text, colLabels=cols, loc='upper center', cellLoc='right')
        tab.auto_set_font_size(False)
        tab.set_fontsize(8.5)
        tab.scale(1, 2.55)
        for (i, j), cell in tab.get_celld().items():
            cell.set_edgecolor('#dddddd')
            if j == 0 and i > 0:
                cell._loc = 'left'
                cell.set_text_props(ha='left')
            if i == 0:
                cell.set_facecolor(CATEGORY_STYLE[CATEGORIES[j - 2]][0] if j >= 2 else '#1C2B33')
                cell.set_text_props(color='white', fontweight='bold', ha='center')
                cell.set_height(cell.get_height() * 1.5)
        tab.auto_set_column_width(range(len(cols)))
        f.text(0.03, 0.04, SOURCE, **foot)
        pdf.savefig(f)

        # one page per city
        for _, r in summary.iterrows():
            f = _page()
            f.text(0.04, 0.945, r['City'], fontsize=26, fontweight='bold', color='#1C2B33')
            f.text(0.04, 0.895, f"{r['Region']}  |  {r['Total km']:,.0f} km of cycling-legal network", fontsize=12, color='#666666')
            ax = f.add_axes([0.02, 0.07, 0.62, 0.80])
            ax.imshow(mpimg.imread(str(map_dir / f"{r['Slug']}_map.png")))
            ax.set_axis_off()
            _pdf_table(f, 0.67, 0.80, 0.30, _city_table(r))
            f.text(0.04, 0.03, SOURCE, **foot)
            pdf.savefig(f)
    return out_path
