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

One INE municipality, one population (added 2026-11):
GADM stores some municipalities as several polygon rows - a main body plus
small separate pieces (islands, exclaves, parts in another comarca): 54 names
on 58 extra rows in Spain, e.g. Valencia 133.2 + 2.9 km2, Zaragoza
1,058.7 + 4.3, Palma 197.6 (+ Cabrera) + 14.1. Matching by name used to give
EVERY piece the municipality's full population, so ~4.3 million people were
counted twice (and the small pieces showed absurd densities, e.g. Alicante
678,187/km2). This module now:
  * resolves each GADM row to an INE municipality code (CPRO + CMUN) -
    assign_ine_codes() - and puts the population on ONE row per code, the
    largest-area piece; the other pieces keep `ine_code` but get NaN
    population. A name that is ambiguous within a province (two INE
    municipalities sharing a candidate name) is left unmatched, never
    guessed.
  * checks, before any road is processed, that population reconciles:
    people placed on polygons + people on INE municipalities with no polygon
    == INE's total (reconcile_population()). A double-assignment raises
    instead of silently inflating totals.
  * applies GADM_TO_INE_NAME_ALIASES for names the generic rules cannot
    bridge (currently Palma and Els Alamus).
municipalities.py then merges a split municipality's pieces back into one
output row (municipality_group_key() gives the grouping).
Verified (2026-11) against the live GADM pull: of the 54 duplicated names,
48 match exactly one INE municipality, 0 match two or more, 4 are
joint-administration areas that are not INE municipalities, and the last 2
are Palma and Els Alamus (the aliases above).
"""

import re
import unicodedata
import zipfile
from pathlib import Path

import geopandas as gpd
import numpy as np
import pandas as pd
import requests

INE_POBMUN_URL = 'https://www.ine.es/pob_xls/pobmun.zip'

# GADM (province key, municipality key) -> INE municipality key, for names
# the generic rules (article reorder, accents, '/' halves) cannot bridge.
# Both sides are _normalize()d forms. Found by checking every INE municipality
# that no GADM row matched (2026-11) - extend this as more are verified.
GADM_TO_INE_NAME_ALIASES = {
    # GADM 'PalmadeMallorca' (Baleares) vs INE 'Palma' (Balears, Illes): 434,786 people
    ('baleares', 'palmademallorca'): 'palma',
    # GADM 'ElAlamus' (Lleida) vs INE 'Alamus, Els' -> 'Els Alamus'
    ('lleida', 'elalamus'): 'elsalamus',
    # --- verified 2026-11: same province, same place under a Spanish/regional-language variant,
    # --- polygon located where the real town is (see CLAUDE.md, 'Readable municipality names').
    ('castellon', 'villarreal'): 'vilareal',   # Vila-real <- GADM Villarreal (53,623 people)
    ('baleares', 'santaeulaliadelrio'): 'santaeulariadesriu',   # Santa Eulària des Riu <- GADM SantaEulaliadelRío (42,479 people)
    ('baleares', 'mahon'): 'mao',   # Maó <- GADM Mahón (30,666 people)
    ('castellon', 'almazora'): 'almassora',   # Almassora <- GADM Almazora (29,159 people)
    ('alicante', 'calpe'): 'calp',   # Calp <- GADM Calpe (27,616 people)
    ('navarra', 'egues'): 'eguesibar',   # Valle de Egüés/Eguesibar <- GADM Egüés (22,825 people)
    ('vizcaya', 'mungai'): 'mungia',   # Mungia <- GADM Mungai (18,129 people)
    ('tarragona', 'santcarlesdelarapita'): 'larapita',   # La Ràpita <- GADM SantCarlesdelaRàpita (16,119 people)
    ('vizcaya', 'sopelana'): 'sopela',   # Sopela <- GADM Sopelana (15,048 people)
    ('navarra', 'estella'): 'estellalizarra',   # Estella-Lizarra <- GADM Estella (14,317 people)
    ('laspalmas', 'santamariadeguiadegrancana'): 'santamariadeguiadegrancanaria',   # Santa María de Guía de Gran Canaria <- GADM SantaMaríadeGuíadeGranCana (14,311 people)
    ('girona', 'castellplatjadaro'): 'castelldaroplatjadaroisagaro',   # Castell d'Aro, Platja d'Aro i s'Agaró <- GADM Castell-Platjad'Aro (12,982 people)
    ('vizcaya', 'etxebarrianteiglesiadesanes'): 'etxebarri',   # Etxebarri <- GADM Etxebarri,AnteiglesiadeSanEs (12,038 people)
    ('barcelona', 'biguesiriells'): 'biguesiriellsdelfai',   # Bigues i Riells del Fai <- GADM BiguesiRiells (10,139 people)
    ('valencia', 'monserrat'): 'montserrat',   # Montserrat <- GADM Monserrat (10,062 people)
    ('valencia', 'rafelbunol'): 'rafelbunyol',   # Rafelbunyol <- GADM Rafelbuñol (9,974 people)
    ('vizcaya', 'abantoyciervanaabantozierben'): 'abantoyciervanaabantozierbena',   # Abanto y Ciérvana-Abanto Zierbena <- GADM AbantoyCiérvana-AbantoZierben (9,379 people)
    ('navarra', 'noain'): 'noainelortzibar',   # Noáin (Valle de Elorz)/Noain (Elortzibar) <- GADM Noáin (8,486 people)
    ('tarragona', 'rodadebara'): 'rodadebera',   # Roda de Berà <- GADM RodadeBarà (8,260 people)
    ('granada', 'otura'): 'villadeotura',   # Villa de Otura <- GADM Otura (7,696 people)
    ('laspalmas', 'sannicolasdetolentino'): 'laaldeadesannicolas',   # La Aldea de San Nicolás <- GADM SanNicolásdeTolentino (7,401 people)
    ('tarragona', 'vandellosilhospitaletdelin'): 'vandellosilhospitaletdelinfant',   # Vandellòs i l'Hospitalet de l'Infant <- GADM Vandellòsil'Hospitaletdel'In (7,143 people)
    ('segovia', 'sanildefonso'): 'realsitiodesanildefonso',   # Real Sitio de San Ildefonso <- GADM SanIldefonso (5,217 people)
    ('barcelona', 'elpapio'): 'elpapiol',   # El Papiol <- GADM ElPapio (4,404 people)
    ('burgos', 'villarcayodemerindaddecastil'): 'villarcayodemerindaddecastillalavieja',   # Villarcayo de Merindad de Castilla la Vieja <- GADM VillarcayodeMerindaddeCastil (4,103 people)
    ('navarra', 'orcoyen'): 'orkoien',   # Orkoien <- GADM Orcoyen (4,098 people)
    ('baleares', 'vilafrancdebonany'): 'vilafrancadebonany',   # Vilafranca de Bonany <- GADM VilafrancdeBonany (3,929 people)
    ('tarragona', 'lesplugadefrancoliesplugad'): 'lesplugadefrancoli',   # L'Espluga de Francolí <- GADM L'EsplugadeFrancolí|Esplugad (3,863 people)
    ('sevilla', 'lalantejuela'): 'lantejuela',   # Lantejuela <- GADM LaLantejuela (3,857 people)
    ('guipuzcoa', 'soraluze'): 'soraluzeplacenciadelasarmas',   # Soraluze-Placencia de las Armas <- GADM Soraluze (3,857 people)
    ('alava', 'oyon'): 'oyonoion',   # Oyón-Oion <- GADM Oyón (3,476 people)
    ('alicante', 'jalon'): 'xalo',   # Xaló <- GADM Jalón (3,104 people)
    ('valencia', 'genoves'): 'elgenoves',   # El Genovés <- GADM Genovés (2,822 people)
    ('valencia', 'realdegandia'): 'elrealdegandia',   # El Real de Gandia <- GADM RealdeGandía (2,722 people)
    ('valencia', 'benisano'): 'benissano',   # Benissanó <- GADM Benisanó (2,449 people)
    ('alava', 'legutiano'): 'legutio',   # Legutio <- GADM Legutiano (2,094 people)
    ('valencia', 'masalaves'): 'massalaves',   # Massalavés <- GADM Masalavés (1,905 people)
    ('valencia', 'alqueriadelacondesa'): 'lalqueriadelacomtessa',   # L'Alqueria de la Comtessa <- GADM AlqueríadelaCondesa (1,547 people)
    ('girona', 'cruillesmonellsisantsadurni'): 'cruillesmonellsisantsadurnidelheura',   # Cruïlles, Monells i Sant Sadurní de l'Heura <- GADM Cruïlles,MonellsiSantSadurní (1,350 people)
    ('zaragoza', 'lajoyosaj'): 'lajoyosa',   # La Joyosa <- GADM LaJoyosaJ (1,156 people)
    ('castellon', 'benlloch'): 'benlloc',   # Benlloc <- GADM Benlloch (1,143 people)
    ('valencia', 'algimiadealfara'): 'algimiadalfara',   # Algímia d'Alfara <- GADM AlgimiadeAlfara (1,115 people)
    ('alava', 'arrazuaubarrundia'): 'arratzuaubarrundia',   # Arratzua-Ubarrundia <- GADM Arrazua-Ubarrundia (1,056 people)
    ('castellon', 'benasal'): 'benassal',   # Benassal <- GADM Benasal (1,048 people)
    ('vizcaya', 'arrankudiaga'): 'arrankudiagazollo',   # Arrankudiaga-Zollo <- GADM Arrankudiaga (1,018 people)
    ('castellon', 'lesuseras'): 'useras',   # Les Useres/Useras <- GADM LesUseras (954 people)
    ('valencia', 'novele'): 'novetle',   # Novetlè <- GADM Novelé (872 people)
    ('baleares', 'deya'): 'deia',   # Deià <- GADM Deyá (741 people)
    ('castellon', 'chert'): 'xert',   # Xert <- GADM Chert (702 people)
    ('cordoba', 'losblazque'): 'losblazquez',   # Los Blázquez <- GADM LosBlázque (619 people)
    ('alicante', 'binigembla'): 'benigembla',   # Benigembla <- GADM Binigembla (590 people)
    ('alicante', 'valldegallinera'): 'lavalldegallinera',   # La Vall de Gallinera <- GADM ValldeGallinera (583 people)
    ('valencia', 'guadasequies'): 'guadassequies',   # Guadasséquies <- GADM Guadasequies (478 people)
    ('madrid', 'gargantilladellozoyaypinilla'): 'gargantilladellozoyaypinilladebuitrago',   # Gargantilla del Lozoya y Pinilla de Buitrago <- GADM GargantilladelLozoyayPinilla (396 people)
    ('castellon', 'vistabelladelmaestrazgo'): 'vistabelladelmaestrat',   # Vistabella del Maestrat <- GADM VistabelladelMaestrazgo (354 people)
    ('huesca', 'torla'): 'torlaordesa',   # Torla-Ordesa <- GADM Torla (342 people)
    ('vizcaya', 'garay'): 'garai',   # Garai <- GADM Garay (332 people)
    ('zaragoza', 'sanmartindelavirgendemonca'): 'sanmartindelavirgendemoncayo',   # San Martín de la Virgen de Moncayo <- GADM SanMartíndelaVirgendeMonca (266 people)
    ('alicante', 'valldeebo'): 'lavalldebo',   # La Vall d'Ebo <- GADM ValldeEbo (231 people)
    ('navarra', 'atez'): 'atetz',   # Atetz <- GADM Atez (227 people)
    ('madrid', 'horcajodelasierra'): 'horcajodelasierraaoslos',   # Horcajo de la Sierra-Aoslos <- GADM HorcajodelaSierra (219 people)
    ('valencia', 'benisuera'): 'benissuera',   # Benissuera <- GADM Benisuera (196 people)
    ('navarra', 'orbaitzeta'): 'orbaizeta',   # Orbaizeta <- GADM Orbaitzeta (186 people)
    ('castellon', 'aresdelmaestre'): 'aresdelmaestrat',   # Ares del Maestrat <- GADM AresdelMaestre (181 people)
    ('castellon', 'torreendomenech'): 'latorredendomenec',   # La Torre d'en Doménec <- GADM TorreEndoménech (177 people)
    ('avila', 'sanmartindelavegadelalberc'): 'sanmartindelavegadelalberche',   # San Martín de la Vega del Alberche <- GADM SanMartíndelaVegadelAlberc (163 people)
    ('navarra', 'oitz'): 'oiz',   # Oiz <- GADM Oitz (129 people)
    ('segovia', 'montejodelavegadelaserrezu'): 'montejodelavegadelaserrezuela',   # Montejo de la Vega de la Serrezuela <- GADM MontejodelaVegadelaSerrezu (121 people)
    ('castellon', 'sarratella'): 'laserratella',   # La Serratella <- GADM Sarratella (113 people)
    ('burgos', 'santamariarivarredonda'): 'santamariaribarredonda',   # Santa María Ribarredonda <- GADM SantaMaríaRivarredonda (97 people)
    ('castellon', 'herbes'): 'herbers',   # Herbers <- GADM Herbés (68 people)
    ('teruel', 'villanuevadelrebollardelasi'): 'villanuevadelrebollardelasierra',   # Villanueva del Rebollar de la Sierra <- GADM VillanuevadelRebollardelaSi (49 people)
    ('burgos', 'castrillomatajudios'): 'castrillomotadejudios',   # Castrillo Mota de Judíos <- GADM CastrilloMatajudíos (48 people)
}

# Trailing ", <article>" alphabetization suffix INE uses for some province
# and municipality names (Castilian/Catalan/Balearic/Galician articles).
# Order matters only in that longer alternatives (e.g. 'Illes' vs 'Les')
# don't collide here - none do. Galician O/As/Os added 2026-11: without them
# 20 Galician municipalities ('Porriño, O', 'Grove, O', 'Pontes de García
# Rodríguez, As', ...) never matched their GADM rows ('OPorriño', ...).
# The article must be the WHOLE tail after the comma, so a comma that is part of
# the name ("Castell d'Aro, Platja d'Aro i s'Agaró") is left alone.
_ARTICLE_SUFFIX = re.compile(
    r"^(.*),\s*(El|La|Los|Las|Lo|Els|Les|Illes|Es|Sa|Ses|A|O|As|Os|L')$",
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
    # INE's official municipality code: 2-digit province + 3-digit municipality.
    df['ine_code'] = df['CPRO'].str.zfill(2) + df['CMUN'].str.zfill(3)
    return df[['ine_code', 'province_name', 'municipality_name', 'population']]


def ine_display_name(name: str) -> str:
    """
    INE's municipality name as it should be READ, not sorted: the trailing
    ", <article>" is moved back to the front and capitalized ('Tres Villas, Las'
    -> 'Las Tres Villas', "Orxa, l'" -> "L'Orxa"). A bilingual 'A/B' name keeps
    BOTH halves, each fixed on its own ('Vila Joiosa, la/Villajoyosa' ->
    'La Vila Joiosa/Villajoyosa'): 9 INE names carry the article on the first half.
    """
    parts = []
    for part in str(name).split('/'):
        part = part.strip()
        moved = _reorder_trailing_article(part)
        if moved != part:
            moved = moved[0].upper() + moved[1:]
        parts.append(moved)
    return '/'.join(parts)


def ine_names_by_code(ine_df: pd.DataFrame) -> pd.Series:
    """INE code -> display name (ine_display_name) for every row of ine_df."""
    codes = _ine_codes(ine_df)
    return pd.Series([ine_display_name(n) for n in ine_df['municipality_name']], index=codes.to_numpy())


def _ine_codes(ine_df: pd.DataFrame) -> pd.Series:
    """The INE code per row of ine_df (positional), synthesizing a stable
    stand-in from province+name when the frame has no `ine_code` column
    (older callers / tests that only pass the three name/population columns)."""
    if 'ine_code' in ine_df.columns:
        return ine_df['ine_code'].astype(str).reset_index(drop=True)
    return (ine_df['province_name'].astype(str) + '|' + ine_df['municipality_name'].astype(str)).reset_index(drop=True)


def assign_ine_codes(municipalities_gdf: gpd.GeoDataFrame, ine_df: pd.DataFrame) -> pd.Series:
    """
    The INE municipality code for every row of municipalities_gdf (NaN where
    none can be established), indexed like municipalities_gdf.

    A GADM row resolves to a code only when exactly ONE INE municipality in
    its province carries that normalized name (after GADM_TO_INE_NAME_ALIASES).
    Zero candidates -> unmatched; two or more -> ambiguous, also unmatched
    (the old name->population dict silently let the last one win).
    """
    codes = _ine_codes(ine_df)
    lookup = {}
    for code, prov, name in zip(codes, ine_df['province_name'], ine_df['municipality_name']):
        pk = _normalize_province(prov)
        for cand in _municipality_candidates(name):
            lookup.setdefault((pk, cand), set()).add(code)

    prov_keys = municipalities_gdf['NAME_2'].map(_normalize_province)
    muni_keys = municipalities_gdf['NAME_4'].map(lambda s: _normalize(_reorder_trailing_article(s)))
    resolved, ambiguous = [], []
    for pk, mk in zip(prov_keys, muni_keys):
        found = lookup.get((pk, GADM_TO_INE_NAME_ALIASES.get((pk, mk), mk)), set())
        if len(found) == 1:
            resolved.append(next(iter(found)))
        else:
            resolved.append(np.nan)
            if len(found) > 1:
                ambiguous.append((pk, mk))
    if ambiguous:
        print(f'WARNING: {len(ambiguous)} GADM rows match more than one INE municipality in their '
              f'province and were left unmatched: {ambiguous[:5]}')
    return pd.Series(resolved, index=municipalities_gdf.index, dtype=object)


def municipality_group_key(municipalities_gdf: gpd.GeoDataFrame) -> pd.Series:
    """
    A key shared by every GADM row (piece) of the same municipality, indexed
    like municipalities_gdf: 'ine:<code>' when the row resolved to an INE
    municipality, else 'name:<province>|<name>' so unmatched same-name pieces
    (e.g. joint-administration areas) still group together.
    """
    pk = municipalities_gdf['NAME_2'].map(_normalize_province)
    mk = municipalities_gdf['NAME_4'].map(lambda s: _normalize(_reorder_trailing_article(s)))
    name_key = 'name:' + pk + '|' + mk
    if 'ine_code' not in municipalities_gdf.columns:
        return name_key
    code = municipalities_gdf['ine_code']
    return pd.Series(np.where(code.notna(), 'ine:' + code.astype(str), name_key), index=municipalities_gdf.index)


def reconcile_population(matched: gpd.GeoDataFrame, ine_df: pd.DataFrame, *, tolerance: float = 0.5) -> dict:
    """
    Invariant check for the INE join: people placed on polygons + people on INE
    municipalities that received no polygon == INE's total. It can only fail if
    a municipality's population was placed twice (or an INE row was dropped),
    which is exactly the double-counting bug this guards against - so a failure
    raises instead of letting inflated totals through. Returns a report dict
    (also printed as one line).
    """
    placed = matched.loc[matched['population'].notna(), 'ine_code']
    if placed.duplicated().any():
        dup = placed[placed.duplicated()].iloc[0]
        raise ValueError(f'INE municipality {dup} received population on more than one GADM polygon')
    codes = _ine_codes(ine_df)
    ine_total = float(ine_df['population'].sum())
    assigned = float(matched['population'].sum())
    unplaced = ine_df.loc[~codes.isin(set(placed)).values]
    gap = ine_total - assigned - float(unplaced['population'].sum())
    if abs(gap) > tolerance:
        raise ValueError(f'INE population does not reconcile: total {ine_total:,.0f}, placed {assigned:,.0f}, '
                         f'unplaced {unplaced["population"].sum():,.0f}, gap {gap:,.0f}')
    report = dict(ine_total=ine_total, assigned=assigned, unplaced_count=len(unplaced),
                  unplaced_population=float(unplaced['population'].sum()))
    print(f'INE reconciliation OK: {assigned:,.0f} of {ine_total:,.0f} people placed on polygons; '
          f'{len(unplaced)} INE municipalities ({report["unplaced_population"]:,.0f} people) have no GADM polygon.')
    return report


def match_ine_to_gadm(municipalities_gdf: gpd.GeoDataFrame, ine_df: pd.DataFrame) -> gpd.GeoDataFrame:
    """
    Resolves every GADM row to an INE municipality (assign_ine_codes: normalized
    province + municipality name, plus GADM_TO_INE_NAME_ALIASES) and places that
    municipality's population on ONE row - the largest-area piece when GADM
    splits it over several polygons. Expects municipalities_gdf to carry GADM's
    NAME_2 (province) and NAME_4 (municipality) columns, and ideally `area_km2`
    (used only to rank pieces; computed equal-area from the geometry if absent).
    Spain-only, unlike population.py's compute_population_stats() which is
    generic across all 5 countries.

    Adds three columns:
      ine_code   INE code of the municipality this row belongs to (NaN if none)
      ine_main   True for the row that carries the population (a municipality's
                 largest piece, or any unmatched row); False for extra pieces
      population float; NaN on unmatched rows AND on extra pieces (never 0.0, so
                 a real-but-unmatched municipality isn't confused with an empty one)
    Returns a copy; does not mutate the input. Prints unmatched pairs and
    runs reconcile_population() before returning.
    """
    out = municipalities_gdf.copy()
    out['ine_code'] = assign_ine_codes(out, ine_df)

    codes = _ine_codes(ine_df)
    pop_by_code = pd.Series(ine_df['population'].to_numpy(), index=codes.to_numpy())
    if pop_by_code.index.duplicated().any():
        raise ValueError('ine_df has duplicate INE codes')

    if 'area_km2' in out.columns:
        area = out['area_km2']
    else:
        try:
            area = out.geometry.to_crs('EPSG:6933').area / 1e6     # equal-area, ranking only
        except Exception:
            area = out.geometry.area
    ranked = out.assign(_area=area).sort_values('_area', ascending=False, kind='stable')
    first_of_code = ~ranked['ine_code'].duplicated()
    is_main = (first_of_code | ranked['ine_code'].isna()).reindex(out.index)
    out['ine_main'] = is_main.astype(bool)
    pop = out['ine_code'].map(pop_by_code)
    out['population'] = pop.where(out['ine_main'], np.nan).astype(float)

    unmatched = out[out['ine_code'].isna()]
    if len(unmatched) > 0:
        pct = len(unmatched) / len(out) * 100
        print(f'WARNING: {len(unmatched)}/{len(out)} municipalities ({pct:.1f}%) had no INE '
              f'population match - Population/Population density left blank for these '
              f'(see the cycling_analysis/ine.py module docstring and CLAUDE.md Known gaps).')
        sample = list(zip(unmatched['NAME_2'], unmatched['NAME_4']))[:10]
        print(f'  Sample unmatched (province, municipality): {sample}')
    pieces = int((~out['ine_main']).sum())
    if pieces:
        print(f'{pieces} GADM rows are extra pieces of a split municipality: population placed on the '
              f'largest piece only (merged back into one row in the outputs - see municipalities.py).')

    reconcile_population(out, ine_df)
    return out
