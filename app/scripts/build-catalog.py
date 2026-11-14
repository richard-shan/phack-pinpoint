#!/usr/bin/env python3
"""Build the local CoStar catalog from the downloaded hackathon bundle.

No network calls, credentials, nearest-neighbor joins, or inferred route facts.
"""
import argparse
import csv
import hashlib
import json
import re
import shutil
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path

APP = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = APP.parent / '.local-data'


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def normalized(value):
    replacements = {'road': 'rd', 'street': 'st', 'avenue': 'ave', 'boulevard': 'blvd',
                    'drive': 'dr', 'parkway': 'pky', 'pkwy': 'pky', 'freeway': 'fwy',
                    'east': 'e', 'west': 'w', 'north': 'n', 'south': 's'}
    tokens = re.findall(r'[a-z0-9]+', (value or '').lower())
    return [replacements.get(x, x) for x in tokens]


def street(address):
    return ' '.join(str(address[k]) for k in
                    ('streetNumber', 'streetDirection', 'streetName', 'streetType', 'streetSuffix') if address.get(k)).strip()


def address_matches(raw, indexed):
    """Require the same street number and street name, not proximity alone."""
    tokens = normalized(indexed.get('address'))
    if not tokens:
        return False
    if indexed.get('state') and indexed['state'] != raw.get('state'):
        return False
    if indexed.get('city') and indexed['city'].casefold() != (raw.get('city') or '').casefold():
        return False
    nums = re.findall(r'^\d+', indexed.get('address', ''))
    if not nums or nums[0] != str(raw.get('streetNumber') or ''):
        return False
    name_tokens = normalized(raw.get('streetName'))
    return bool(name_tokens) and all(t in tokens for t in name_tokens)


def number(value):
    try:
        return float(value) if value not in ('', None) else None
    except (TypeError, ValueError):
        return None


def build(source, out):
    data_dir = out / 'data'
    records_dir = data_dir / 'records'
    image_dir = out / 'assets/real'
    records_dir.mkdir(parents=True, exist_ok=True)
    image_dir.mkdir(parents=True, exist_ok=True)
    property_path = source / 'costar/data/commercial-property.jsonl'
    model_path = source / 'models_index.csv'
    enum_path = source / 'costar/schemas/enums.json'
    enums = json.loads(enum_path.read_text())
    records = [json.loads(line) for line in property_path.read_text().splitlines() if line.strip()]
    with model_path.open(newline='') as file:
        model_rows = list(csv.DictReader(file))
    by_property = defaultdict(list)
    for row in model_rows:
        # Deliberately exclude internal_id, address-only matches, and proximity guesses.
        if row.get('costar_property_id'):
            by_property[row['costar_property_id']].append(row)
    properties = []
    join_counts = Counter()
    copied_images = 0
    for raw in records:
        addr = raw.get('primaryAddress') or {}
        if ((addr.get('city') or '').casefold(), addr.get('state')) not in {('houston', 'TX'), ('los angeles', 'CA')}:
            continue
        pid = str(raw['id'])
        property_models = []
        source_models = []
        all_addresses = raw.get('addresses') or [addr]
        for model in by_property[pid]:
            sid = model['sid']
            # Include address candidates for inspection, but only verified rows expose imagery.
            matched_address = next((a for a in all_addresses if address_matches(a, model)), None)
            join_status = 'verified' if matched_address else ('address-unconfirmed' if not re.match(r'^\d+', model.get('address', '')) else 'address-mismatch')
            join_counts[join_status] += 1
            notes = ['Property ID agrees with the CoStar record.']
            notes.append('Street number and street name agree with a CoStar address.' if matched_address else 'Address does not establish a verified join. Do not use this model as location evidence.')
            notes.append('Model name is a source label, not a confirmed caller location.')
            images = []
            pano_points = []
            cache = source / 'aed_visual' / (sid + '.json')
            cached = json.loads(cache.read_text()).get('data', {}).get('model') if cache.exists() else None
            if cached and join_status == 'verified':
                panos = cached.get('panoLocations') or []
                for n, pano in enumerate(panos):
                    pano_points.append({'id': pano['id'], 'label': pano.get('label'), 'position': pano.get('position')})
                # Actual saved cube faces: select up to six views, retain original bytes.
                for n, face in [(0, 1), (0, 2), (1, 1), (1, 2), (2, 1), (2, 2)]:
                    local = source / 'aed_visual/images' / sid / f'{n:03d}_{face}.jpg'
                    if n >= len(panos) or not local.is_file():
                        continue
                    pano = panos[n]
                    target = image_dir / sid / local.name
                    target.parent.mkdir(exist_ok=True)
                    if not target.exists() or digest(target) != digest(local):
                        shutil.copyfile(local, target)
                    children = (pano.get('skybox') or {}).get('children') or []
                    origin = children[face].split('?', 1)[0] if len(children) > face else None
                    images.append({'url': f'/assets/real/{sid}/{local.name}',
                        'caption': f"{model['name']} · source panorama {pano.get('label') or n + 1}, cube face {face}",
                        'nodeId': pano['id'], 'position': pano.get('position'),
                        'source': {'provider': 'Matterport', 'modelId': sid, 'panoId': pano['id'],
                                   'cubeFace': face, 'urlWithoutAccessParameters': origin,
                                   'sha256': digest(local), 'recordUrl': f'/data/records/{pid}.json'}})
                copied_images += len(images)
            if not images:
                notes.append('No verified local image is included for this model.')
            property_models.append({'id': sid, 'name': model['name'], 'address': model['address'],
                'scanDate': None, 'modelCreatedDate': model.get('created') or None,
                'originalModelCreatedDate': model.get('original_created') or None,
                'url': 'https://my.matterport.com/show/?m=' + sid,
                'images': images, 'joinStatus': join_status, 'notes': notes,
                'panoCount': int(model['pano_count']) if model.get('pano_count', '').isdigit() else None,
                'panoPoints': pano_points,
                'source': {'file': 'models_index.csv', 'recordId': sid, 'propertyId': pid}})
            source_models.append({k: model.get(k) for k in ['sid','name','address','city','state','costar_property_id','original_created','created','pano_count','floor_count','room_count']})
        property_models.sort(key=lambda m: (not bool(m['images']), m['joinStatus'] != 'verified', m['name'], m['id']))
        completed = [(i, e) for i, e in enumerate(raw.get('constructionEvents') or []) if e.get('eventType') == 2 and e.get('year')]
        year_entry = min(completed, key=lambda x: x[1]['year']) if completed else None
        sqft_key = 'grossBuildingAreaSquareFeet' if raw.get('grossBuildingAreaSquareFeet') is not None else 'rentableBuildingAreaSquareFeet'
        display_address = ', '.join(x for x in [street(addr), addr.get('city'), ' '.join(x for x in [addr.get('state'), str(addr.get('postcode') or '')[:5]] if x)] if x)
        fields = {'id': 'id', 'name': 'buildingName', 'address': 'primaryAddress', 'city': 'primaryAddress.city',
            'state': 'primaryAddress.state', 'postalCode': 'primaryAddress.postcode', 'useType': 'propertyType',
            'subtype': 'propertySubtype', 'stories': 'numStoriesAboveGrade',
            'yearBuilt': f'constructionEvents[{year_entry[0]}].year' if year_entry else None,
            'sqft': sqft_key, 'latitude': 'primaryAddress.latitude', 'longitude': 'primaryAddress.longitude'}
        prop = {'id': pid, 'name': raw.get('buildingName') or street(addr), 'address': display_address,
            'city': 'Houston' if addr.get('state') == 'TX' else 'Los Angeles', 'state': addr.get('state'),
            'postalCode': str(addr.get('postcode') or '')[:5] or None,
            'useType': enums.get('PropertyType', {}).get(str(raw.get('propertyType'))),
            'subtype': enums.get('PropertySubtype', {}).get(str(raw.get('propertySubtype'))),
            'stories': raw.get('numStoriesAboveGrade'), 'yearBuilt': year_entry[1]['year'] if year_entry else None,
            'sqft': raw.get(sqft_key), 'sqftBasis': 'Gross building area' if sqft_key.startswith('gross') else 'Rentable building area',
            'latitude': number(addr.get('latitude')), 'longitude': number(addr.get('longitude')),
            'source': {'file': 'commercial-property.jsonl', 'recordId': pid, 'fields': fields,
                       'recordUrl': f'/data/records/{pid}.json', 'enumFile': '/data/enums.json',
                       'nameFallback': 'primaryAddress street' if not raw.get('buildingName') else None},
            'models': property_models,
            'unknowns': ['Caller location has not been automatically determined.',
                'Entrance, staging, interior route and stretcher access are not verified.',
                'AEDs, hazards, door status and current tenant occupancy are not verified.',
                'Matterport scan date is not established by model creation dates.',
                'Vexcel imagery is historical; capture time and current access require confirmation.']}
        if any(m['joinStatus'] != 'verified' for m in property_models):
            prop['unknowns'].append('Some indexed models have an unresolved property/address join and are excluded from image evidence.')
        if not any(m['images'] for m in property_models):
            prop['unknowns'].append('No verified Matterport image is cached locally for this property.')
        properties.append(prop)
        record = {'provider': 'CoStar hackathon data bundle', 'sourceFile': 'commercial-property.jsonl',
                  'recordId': pid, 'record': raw, 'matterportIndexRows': source_models,
                  'normalization': fields, 'note': 'Source snapshot, not a real-time verification. No caller, route, or equipment claims are inferred.'}
        (records_dir / f'{pid}.json').write_text(json.dumps(record, ensure_ascii=False, indent=2) + '\n')
    # Medical lobby first; then verified offline-image properties, then searchable inventory.
    properties.sort(key=lambda p: (p['id'] != '350372', not any(m['images'] for m in p['models']), p['city'], p['name'].casefold(), p['id']))
    stats = {'properties': len(properties), 'houston': sum(p['city'] == 'Houston' for p in properties),
             'losAngeles': sum(p['city'] == 'Los Angeles' for p in properties),
             'indexedModels': sum(len(p['models']) for p in properties),
             'verifiedModels': join_counts['verified'], 'unconfirmedModels': join_counts['address-unconfirmed'],
             'mismatchedModels': join_counts['address-mismatch'],
             'propertiesWithLocalImages': sum(any(m['images'] for m in p['models']) for p in properties),
             'localImages': copied_images}
    result = {'generatedAt': datetime.now(timezone.utc).isoformat(),
        'source': {'name': 'CoStar hackathon data bundle + Matterport model index', 'mode': 'offline source snapshot',
                   'propertyFile': 'commercial-property.jsonl', 'propertySha256': digest(property_path),
                   'modelFile': 'models_index.csv', 'modelSha256': digest(model_path),
                   'enumSha256': digest(enum_path), 'defaultPropertyId': '350372'},
        'stats': stats, 'properties': properties}
    (data_dir / 'catalog.json').write_text(json.dumps(result, ensure_ascii=False, separators=(',', ':')) + '\n')
    # Only decoding tables used by the UI are exposed; no credentials or signed image URLs.
    (data_dir / 'enums.json').write_text(json.dumps({k: enums[k] for k in ['PropertyType','PropertySubtype','ConstructionEventType']}, indent=2) + '\n')
    print(json.dumps(stats, indent=2))


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    parser.add_argument('--output', type=Path, default=APP)
    args = parser.parse_args()
    build(args.source, args.output)
