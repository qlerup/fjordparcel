"""Validate carrier-supplied delivery windows without inventing an ETA."""
from datetime import datetime


def _iso(value):
    if not isinstance(value, str) or len(value) > 80:
        return ''
    value = value.strip()
    try:
        parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
        if not 2000 <= parsed.year <= 2200:
            return ''
        # A delivery date is displayed in the carrier's supplied calendar date.
        return value if len(value) == 10 else parsed.isoformat(timespec='seconds')
    except ValueError:
        return ''


def normalize_estimate(value):
    if not isinstance(value, dict):
        return {'from': '', 'to': '', 'source': ''}
    start, end = _iso(value.get('from')), _iso(value.get('to'))
    if start and end and start[:10] > end[:10]:
        start, end = '', ''
    source = value.get('source') if value.get('source') in ('Official', '17TRACK') else ''
    return {'from': start, 'to': end, 'source': source if start or end else ''}


def display_estimate(start, end, source):
    estimate = normalize_estimate({'from': start, 'to': end, 'source': source})
    start, end = estimate['from'], estimate['to']
    def day(value):
        return datetime.fromisoformat(value).strftime('%d-%m-%Y')
    if start and end:
        label = day(start) if start[:10] == end[:10] else f'{day(start)} – {day(end)}'
    elif start:
        label = f'Tidligst {day(start)}'
    elif end:
        label = f'Senest {day(end)}'
    else:
        return None
    return {'label': label, 'source': {'Official': 'Fragtfirmaets estimat',
                                     '17TRACK': '17TRACKs estimat'}.get(estimate['source'], 'Leveringsestimat')}
