"""Offline city lookup and route overview, shared by the website and bot.

No tracking numbers, events or locations are sent to a geocoding service.
"""
from functools import lru_cache
import gzip
import io
import json
import math
from pathlib import Path
import re
import unicodedata

DATA = Path(__file__).parent / 'map_data'
WIDTH, HEIGHT = 960, 460


def normalize(value):
    value = str(value or '').casefold().replace('ø', 'o').replace('æ', 'ae').replace('ß', 'ss')
    value = ''.join(c for c in unicodedata.normalize('NFKD', value) if not unicodedata.combining(c))
    return ' '.join(re.findall(r'[^\W_]+', value, re.UNICODE))


def city_name_keys(city):
    names = set(city[5])
    if city[1] == 'DK':
        # Carriers use both stripped accents (Koge) and Danish digraphs
        # (Koege). Add aliases to Danish places instead of globally replacing
        # "oe"/"aa", which would change genuine foreign place names too.
        names.update(name.casefold().replace('æ', 'ae').replace('ø', 'oe').replace('å', 'aa')
                     for name in city[5])
    return {normalize(name) for name in names}


@lru_cache(maxsize=1)
def gazetteer():
    with gzip.open(DATA / 'cities.json.gz', 'rt', encoding='utf-8') as source:
        cities = json.load(source)
    with gzip.open(DATA / 'countries.json.gz', 'rt', encoding='utf-8') as source:
        countries = json.load(source)
    index, country_index = {}, {}
    for city in cities:
        for alias in city_name_keys(city):
            if len(alias) > 2:
                index.setdefault(alias, []).append(city)
    # Carrier locations also use postal districts, e.g. HOLMEGAARD, DK, DK.
    # These are not necessarily populated-place entries in GeoNames.
    with gzip.open(DATA / 'postal-dk.json.gz', 'rt', encoding='utf-8') as source:
        postal = json.load(source)
    for district in postal:
        for alias in city_name_keys(district):
            if len(alias) > 2 and not any(city[1] == 'DK' for city in index.get(alias, [])):
                index.setdefault(alias, []).append(district)
    for code, aliases, _ in countries:
        for alias in aliases:
            if alias:
                country_index[normalize(alias)] = code
    country_index.update({'danmark': 'DK', 'tyskland': 'DE', 'sverige': 'SE', 'norge': 'NO',
                          'kina': 'CN', 'holland': 'NL', 'storbritannien': 'GB', 'england': 'GB'})
    return index, country_index


@lru_cache(maxsize=2048)
def locate(location):
    index, countries = gazetteer()
    text = normalize(location)
    if not text or text in countries or len(text) > 220:
        return None
    words = text.split()
    # Country clues must be separate location components, not city substrings.
    components = [normalize(part) for part in re.split(r'[,;/|()\[\]]', location)]
    country_codes = {countries[c] for c in components if c in countries}
    tail = re.search(r'\b([A-Z]{2})$', location.strip())
    if tail and tail[1].lower() in countries:
        country_codes.add(countries[tail[1].lower()])
    if len(country_codes) > 1:
        return None
    country = next(iter(country_codes), None)
    candidates = {}
    for length in range(min(6, len(words)), 0, -1):
        for start in range(len(words) - length + 1):
            alias = ' '.join(words[start:start+length])
            # Country-only events do not denote a city.
            if alias in countries and alias not in index:
                continue
            for city in index.get(alias, []):
                if country and city[1] != country:
                    continue
                candidates[(city[1], city[2], city[3])] = city
        if candidates:
            break
    # Preserve meaningful accents: Køge must not collide with Kōge in Japan.
    raw_text = ' ' + ' '.join(re.findall(r'[^\W_]+', location.casefold())) + ' '
    exact = [city for city in candidates.values() if any(
        ' ' + ' '.join(re.findall(r'[^\W_]+', alias.casefold())) + ' ' in raw_text
        for alias in city[5] if alias)]
    ranked = sorted(exact or candidates.values(), key=lambda c: c[4], reverse=True)
    if not ranked:
        return None
    # Avoid guessing between equally plausible towns with the same name.
    if len(ranked) > 1 and ranked[0][4] < max(1, ranked[1][4]) * 5:
        return None
    city = ranked[0]
    return (city[0], city[1], city[2], city[3])


def route_points(events):
    valid = [e for e in events if isinstance(e, dict)]
    # Providers store newest first. ISO dates sort consistently after parsing.
    from datetime import datetime, timezone
    def timestamp(event):
        try:
            date = datetime.fromisoformat(str(event.get('date_iso', '')).replace('Z', '+00:00'))
            return date.replace(tzinfo=date.tzinfo or timezone.utc).timestamp()
        except (ValueError, TypeError):
            return float('-inf')
    ordered = sorted(reversed(valid), key=timestamp)
    route = []
    for event in ordered:
        point = locate(str(event.get('location') or ''))
        if point and (not route or route[-1] != point):
            route.append(point)
    return route


def project(lon, lat):
    lat = math.radians(max(-85, min(85, lat)))
    return lon / 360, -math.log(math.tan(math.pi / 4 + lat / 2)) / (2 * math.pi)


@lru_cache(maxsize=1)
def land_rings():
    with gzip.open(DATA / 'countries.json.gz', 'rt', encoding='utf-8') as source:
        countries = json.load(source)
    rings = []
    for _, _, polygons in countries:
        for polygon in polygons:
            points = [project(lon,lat) for lon,lat in polygon]
            xs, ys = zip(*points)
            rings.append((min(xs), min(ys), max(xs), max(ys), points))
    return rings


def render_map(events):
    route = route_points(events)
    if not route:
        return None
    return _render(tuple(route))


@lru_cache(maxsize=64)
def _render(route):
    from PIL import Image, ImageDraw, ImageFont
    # Unwrap crossings of the date line so the route follows the short way.
    world = []
    for _, _, lat, lon in route:
        x, y = project(lon, lat)
        if world:
            x += round(world[-1][0] - x)
        world.append((x,y))
    xs, ys = zip(*world)
    scale = min((WIDTH-160)/max(max(xs)-min(xs), .022),
                (HEIGHT-160)/max(max(ys)-min(ys), .012))
    cx, cy = (max(xs)+min(xs))/2, (max(ys)+min(ys))/2
    def screen(x,y):
        return (round(WIDTH/2+(x-cx)*scale), round((HEIGHT-30)/2+(y-cy)*scale))
    image = Image.new('RGB', (WIDTH, HEIGHT), '#dcecf2')
    draw = ImageDraw.Draw(image)
    left, right = cx-WIDTH/2/scale, cx+WIDTH/2/scale
    top, bottom = cy-HEIGHT/2/scale, cy+HEIGHT/2/scale
    for xmin,ymin,xmax,ymax,ring in land_rings():
        if ymax < top or ymin > bottom:
            continue
        for offset in range(math.floor(left-xmax), math.ceil(right-xmin)+1):
            if xmax+offset < left or xmin+offset > right:
                continue
            pixels = []
            for x,y in ring:
                pixel = screen(x+offset,y)
                if not pixels or pixel != pixels[-1]:
                    pixels.append(pixel)
            if len(pixels) > 2:
                draw.polygon(pixels, fill='#f1f4ec', outline='#bccbc1')
    points = [screen(x,y) for x,y in world]
    if len(points) > 1:
        draw.line(points, fill='white', width=8, joint='curve')
        draw.line(points, fill='#2778a5', width=4, joint='curve')
        for start,end in zip(points,points[1:]):
            dx,dy = end[0]-start[0],end[1]-start[1]
            distance = math.hypot(dx,dy)
            if distance > 30:
                ux,uy = dx/distance,dy/distance
                mx,my = (start[0]+end[0])/2,(start[1]+end[1])/2
                draw.polygon([(mx+ux*8,my+uy*8),(mx-ux*6-uy*5,my-uy*6+ux*5),
                              (mx-ux*6+uy*5,my-uy*6-ux*5)],fill='#2778a5')
    font = ImageFont.truetype(str(DATA / 'DejaVuSans.ttf'), size=16)
    small = ImageFont.truetype(str(DATA / 'DejaVuSans.ttf'), size=12)
    # One marker per coordinate, including when a parcel revisits an earlier city.
    unique = dict(zip(route, points))
    boxes = []
    for number,(city,(x,y)) in enumerate(unique.items(),1):
        latest = city == route[-1]
        radius = 10 if latest else 7
        draw.ellipse((x-radius,y-radius,x+radius,y+radius), fill='#e28630' if latest else '#2778a5', outline='white', width=3)
        label = f'{number}. {city[0]}'
        box = draw.textbbox((0,0), label, font=font)
        width = box[2]-box[0]+12
        for dy in (-31,14,-53,36):
            lx = max(4,min(WIDTH-width-4,x+12))
            ly = max(4,min(HEIGHT-52,y+dy))
            rect = (lx,ly,lx+width,ly+25)
            if any(rect[0]<b[2] and rect[2]>b[0] and rect[1]<b[3] and rect[3]>b[1] for b in boxes):
                continue
            boxes.append(rect)
            draw.rounded_rectangle(rect, radius=4, fill='white')
            draw.text((lx+6,ly+3),label,font=font,fill='#16384c')
            break
    draw.rectangle((0,HEIGHT-30,WIDTH,HEIGHT),fill='white')
    draw.text((12,HEIGHT-23),'Bypositioner · linjen viser hændelsesrækkefølge · orange: senest kendte sted', font=small,fill='#405466')
    attribution = 'GeoNames (CC BY 4.0) / Natural Earth'
    draw.text((WIDTH-280,HEIGHT-23),attribution,font=small,fill='#405466')
    output = io.BytesIO()
    image.save(output,format='PNG',optimize=True)
    return output.getvalue()
