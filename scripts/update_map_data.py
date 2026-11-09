"""Refresh bundled GeoNames (CC BY 4.0) and Natural Earth (public domain) data."""
import gzip
import io
import json
import sys
from collections import defaultdict
from pathlib import Path
import urllib.request
import zipfile

ROOT = Path(__file__).resolve().parents[1] / 'map_data'
ROOT.mkdir(exist_ok=True)
def update_postal_data():
    with urllib.request.urlopen('https://download.geonames.org/export/zip/DK.zip', timeout=60) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    districts = defaultdict(list)
    for line in archive.read('DK.txt').decode().splitlines():
        fields = line.split('\t')
        districts[fields[2]].append((float(fields[9]), float(fields[10])))
    postal = []
    for name, points in sorted(districts.items()):
        # Postal districts can contain many postal codes; use their centre.
        lat = round(sum(p[0] for p in points)/len(points), 5)
        lon = round(sum(p[1] for p in points)/len(points), 5)
        postal.append([name, 'DK', lat, lon, 0, [name]])
    with gzip.open(ROOT / 'postal-dk.json.gz', 'wt', encoding='utf-8') as out:
        json.dump(postal, out, ensure_ascii=False, separators=(',', ':'))
    print(f'Bundled {len(postal)} Danish postal districts')


update_postal_data()
if '--postal-only' in sys.argv:
    raise SystemExit(0)

rows = {}
for name in ('cities15000', 'DK'):
    with urllib.request.urlopen(f'https://download.geonames.org/export/dump/{name}.zip', timeout=90) as response:
        archive = zipfile.ZipFile(io.BytesIO(response.read()))
    for line in archive.read(name + '.txt').decode().splitlines():
        fields = line.split('\t')
        if fields[6] != 'P' or fields[7] in ('PPLH', 'PPLQ', 'PPLW'):
            continue
        aliases = sorted(set([fields[1], fields[2]] + fields[3].split(',')) - {''})
        rows[fields[0]] = [fields[1], fields[8], float(fields[4]), float(fields[5]), int(fields[14] or 0), aliases]
with gzip.open(ROOT / 'cities.json.gz', 'wt', encoding='utf-8') as out:
    json.dump(list(rows.values()), out, ensure_ascii=False, separators=(',', ':'))
url = 'https://raw.githubusercontent.com/nvkelso/natural-earth-vector/master/geojson/ne_10m_admin_0_countries.geojson'
with urllib.request.urlopen(url, timeout=90) as response:
    countries = json.load(response)['features']
# Keep geometry and useful country aliases only; round to ~100m precision.
result = []
for feature in countries:
    props = feature['properties']
    aliases = [v for k,v in props.items() if k.startswith('NAME') and isinstance(v,str)]
    aliases += [props.get('ISO_A2_EH', ''), props.get('ISO_A3_EH', '')]
    geometry = feature['geometry']
    polygons = geometry['coordinates'] if geometry['type'] == 'MultiPolygon' else [geometry['coordinates']]
    # Exterior rings suffice for this overview map.
    rings = [[[round(x,3),round(y,3)] for x,y,*_ in polygon[0]] for polygon in polygons]
    result.append([props.get('ISO_A2_EH'), aliases, rings])
with gzip.open(ROOT / 'countries.json.gz', 'wt', encoding='utf-8') as out:
    json.dump(result, out, ensure_ascii=False, separators=(',', ':'))
print(f'Bundled {len(rows)} cities and {len(result)} countries')
