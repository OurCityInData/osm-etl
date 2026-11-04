"""
km of cycling infrastructure that each pilot city declared in the RedBici
Observatorio de la Bicicleta survey, used by city_report.py to compare against
the OSM-derived figures.

Source: the "official_*" columns of "Some Spanish (OSM vs Official) municipality
data for reference.xlsx" (sheet "Full Summary", Drive: Observatorio RedBici /
Data and Defintiones). Values are reproduced exactly as reported; they were NOT
validated and some look internally inconsistent (e.g. Zaragoza declares 504 km of
30 km/h streets against a 290 km total; Villena's categories sum to 26 km against
a 5 km total). A missing key means the city gave no figure (shown as "no
reportado"), which is different from a reported 0. San Sebastián, Sant Cugat,
Siero and Vilafranca del Penedès have no figures in that workbook.

The survey also asks about contraflow streets; the current classification does
not produce that category, so it is not compared.
"""

# survey field -> category in cycling_analysis.area_classification.CATEGORIES
SURVEY_TO_CATEGORY = {
    'one_way_side': 'One-way side bike lane',
    'two_way_side': 'Two-way side bike lane',
    'shared_30': 'Shared street at 30 km/h',
    'bike_sidewalk': 'Bike lane on sidewalk',
    'bus_bike': 'Bus-bike lane',
    'pacified_20': 'Pacified street at 20 km/h',
    'pacified_10': 'Pacified zone at 10 km/h',
    'greenway': 'Greenway',
}

# The Observatorio's own table ("Counts as km of bike lane?" = Yes) for the types the
# survey asks about; shared 30 km/h streets and bus-bike lanes are listed there as "No".
NETWORK_CATEGORIES = [
    'Greenway', 'Two-way side bike lane', 'One-way side bike lane',
    'Bike lane on sidewalk', 'Pacified zone at 10 km/h', 'Pacified street at 20 km/h',
]

# city (display name used in scripts/city_maps.py) -> {'total': km or None, <survey field>: km}
SURVEY_KM = {
    'Alicante': {'total': 87.0, 'one_way_side': 6.0, 'two_way_side': 4.0, 'shared_30': 30.0,
                 'bike_sidewalk': 56.0, 'bus_bike': 0.1, 'pacified_20': 0.1, 'pacified_10': 3.0, 'greenway': 16.0},
    'Bilbao': {'total': 35.52, 'one_way_side': 2.33, 'two_way_side': 27.21, 'bike_sidewalk': 5.85,
               'bus_bike': 1.21, 'greenway': 0.56},
    'Fuenlabrada': {'total': 30.0},
    "L'Hospitalet de Llobregat": {'total': 131.0, 'two_way_side': 36.98, 'shared_30': 88.0,
                                  'bike_sidewalk': 6.4, 'bus_bike': 3.13},
    'Lloret de Mar': {'total': 14.0},
    'San Sebastián': {},
    'Sant Cugat del Vallès': {},
    'Siero': {},
    'Valencia': {'total': 251.0, 'one_way_side': 1.0, 'two_way_side': 134.0, 'shared_30': 37.0,
                 'bike_sidewalk': 60.0, 'bus_bike': 7.0, 'pacified_20': 25.0, 'pacified_10': 5.0, 'greenway': 19.0},
    'Vilafranca del Penedès': {},
    'Villena': {'total': 5.0, 'one_way_side': 4.0, 'two_way_side': 1.0, 'bike_sidewalk': 1.0,
                'pacified_20': 5.0, 'pacified_10': 3.0, 'greenway': 12.0},
    'Vitoria-Gasteiz': {'total': 139.8, 'one_way_side': 2.4, 'two_way_side': 41.0, 'shared_30': 19.3,
                        'bike_sidewalk': 38.1, 'bus_bike': 1.0, 'pacified_20': 6.9, 'pacified_10': 56.2, 'greenway': 38.8},
    'Zaragoza': {'total': 290.0, 'one_way_side': 25.7, 'two_way_side': 50.51, 'shared_30': 504.22,
                 'bike_sidewalk': 78.17, 'pacified_20': 20.59, 'pacified_10': 25.75, 'greenway': 84.75},
}


def survey_total(city):
    """Declared total km, or None if the city gave none."""
    return SURVEY_KM.get(city, {}).get('total')


def survey_by_category(city):
    """{category: declared km} - only the categories the city reported."""
    row = SURVEY_KM.get(city, {})
    return {cat: row[field] for field, cat in SURVEY_TO_CATEGORY.items() if field in row}
