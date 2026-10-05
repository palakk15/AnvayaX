"""Bounded, attributable provider forecasts; never fed into the demonstration gate."""
from __future__ import annotations

from datetime import UTC, datetime, timedelta
import hashlib
import json
import math
from pathlib import Path

import requests

from .public_data import _write_atomic, _write_json

ENDPOINT = 'https://api.open-meteo.com/v1/forecast'
LIVE_MODELS = {
    'gfs_global': {'name': 'NOAA GFS Global', 'provider': 'NOAA / NCEP', 'profile_id': 'gfs',
                   'docs': 'https://open-meteo.com/en/docs/gfs-api'},
    'ecmwf_ifs025': {'name': 'ECMWF IFS 0.25°', 'provider': 'ECMWF', 'profile_id': 'ifs_control',
                      'docs': 'https://open-meteo.com/en/docs/ecmwf-api'},
    'ecmwf_aifs025_single': {'name': 'ECMWF AIFS Single 0.25°', 'provider': 'ECMWF', 'profile_id': 'aifs_single',
                             'docs': 'https://open-meteo.com/en/docs/ecmwf-api'},
}
CITIES = {
    'Delhi': (28.6139, 77.2090), 'Mumbai': (19.0760, 72.8777), 'Bengaluru': (12.9716, 77.5946),
    'Chennai': (13.0827, 80.2707), 'Kolkata': (22.5726, 88.3639), 'Hyderabad': (17.3850, 78.4867),
    'Guwahati': (26.1445, 91.7362), 'Jaipur': (26.9124, 75.7873),
}
FIELDS = {'temperature_2m': '°C', 'precipitation': 'mm', 'wind_speed_10m': 'm/s', 'wind_direction_10m': '°'}
BYTE_LIMIT = 2_000_000


def normalize_response(data: dict) -> list[dict]:
    hourly = data.get('hourly', {})
    times = hourly.get('time', [])
    if not 1 <= len(times) <= 96 or data.get('utc_offset_seconds') != 0:
        raise ValueError('Provider must return at most 96 hourly timestamps in UTC.')
    parsed = [datetime.fromisoformat(t).replace(tzinfo=UTC) for t in times]
    if any(b - a != timedelta(hours=1) for a, b in zip(parsed, parsed[1:])):
        raise ValueError('Provider timestamps are not a contiguous hourly series.')
    rows = []
    for model in LIVE_MODELS:
        for field, unit in FIELDS.items():
            key = f'{field}_{model}'
            if key not in hourly or len(hourly[key]) != len(times):
                raise ValueError(f'Provider did not return the requested field: {key}')
            if data.get('hourly_units', {}).get(key) != unit:
                raise ValueError(f'Unexpected provider unit for {key}')
        for i, instant in enumerate(parsed):
            row = {'model': model, 'valid_time': instant.isoformat(),
                   'precipitation_start': (instant - timedelta(hours=1)).isoformat()}
            for field in FIELDS:
                value = hourly[f'{field}_{model}'][i]
                if value is not None and (isinstance(value, bool) or not isinstance(value, (int, float))
                                          or not math.isfinite(value)):
                    raise ValueError('Provider returned an invalid numerical value.')
                if value is not None and field in ('precipitation', 'wind_speed_10m') and value < 0:
                    raise ValueError('Provider returned a negative precipitation or speed value.')
                if value is not None and field == 'wind_direction_10m' and not 0 <= value <= 360:
                    raise ValueError('Provider returned an invalid wind direction.')
                row[field] = value
            speed, direction = row['wind_speed_10m'], row['wind_direction_10m']
            row['u10'] = -speed * math.sin(math.radians(direction)) if None not in (speed, direction) else None
            row['v10'] = -speed * math.cos(math.radians(direction)) if None not in (speed, direction) else None
            rows.append(row)
    if not any(row['temperature_2m'] is not None for row in rows):
        raise ValueError('No temperature forecasts were supplied.')
    return rows


def fetch_public_forecast(city: str, output_dir='artifacts/live', session=None) -> dict:
    if city not in CITIES:
        raise ValueError('Choose a supported city.')
    latitude, longitude = CITIES[city]
    params = {'latitude': latitude, 'longitude': longitude, 'hourly': ','.join(FIELDS),
              'models': ','.join(LIVE_MODELS), 'forecast_days': 3, 'timezone': 'UTC', 'wind_speed_unit': 'ms'}
    response = (session or requests).get(ENDPOINT, params=params, timeout=(5, 25), stream=True,
                                        allow_redirects=False)
    try:
        response.raise_for_status()
        if response.status_code != 200:
            raise ValueError('Only complete HTTP 200 provider responses are accepted.')
        if int(response.headers.get('Content-Length', 0)) > BYTE_LIMIT:
            raise ValueError('Provider response exceeds the 2 MB budget.')
        raw = bytearray()
        for chunk in response.iter_content(65536):
            raw.extend(chunk)
            if len(raw) > BYTE_LIMIT:
                raise ValueError('Provider response exceeds the 2 MB budget.')
        data = json.loads(raw)
        rows = normalize_response(data)
        digest = hashlib.sha256(raw).hexdigest()
        snapshot = {
            'schema_version': 1, 'data_kind': 'real', 'evidence_kind': 'provider_forecast',
            'city': city, 'requested_coordinates': {'latitude': latitude, 'longitude': longitude},
            'returned_coordinates': {k: data.get(k) for k in ('latitude', 'longitude', 'elevation')},
            'retrieved_at': datetime.now(UTC).isoformat(), 'source_url': response.url,
            'request_parameters': params, 'sha256': digest, 'bytes': len(raw),
            'raw_file': f'{digest}.provider.json', 'models': LIVE_MODELS,
            'attribution': 'Weather data by Open-Meteo.com; upstream forecasts by NOAA/NCEP and ECMWF.',
            'license_url': 'https://open-meteo.com/en/terms',
            'documentation_url': 'https://open-meteo.com/en/docs', 'units': FIELDS, 'rows': rows,
            'model_run_initialization': None, 'model_checkpoint_version': None,
            'historical_publication_time_verified': False, 'skill_verified': False,
            'trained_blend_applied': False,
            'notes': ['Named model forecasts served and processed by Open-Meteo; not station observations.',
                      'Latest-series endpoint does not pin a single initialization cycle or checkpoint.',
                      'Hourly output may involve provider interpolation and elevation adjustment.',
                      'Precipitation is the preceding-hour total, including rain, showers and snow water equivalent.',
                      'Null values remain missing. The comparison range is not a calibrated confidence interval.',
                      'These downloads do not retrain or validate the prepared blending gate.'],
        }
        directory = Path(output_dir)
        _write_atomic(directory / snapshot['raw_file'], bytes(raw))
        _write_json(directory / f'{digest}.receipt.json', snapshot)
        _write_json(directory / f'{city.lower()}.latest.json', snapshot)
        return snapshot
    finally:
        response.close()


def load_snapshot(city: str, output_dir='artifacts/live') -> dict | None:
    if city not in CITIES:
        raise ValueError('Choose a supported city.')
    directory = Path(output_dir)
    file = directory / f'{city.lower()}.latest.json'
    if not file.exists():
        return None
    snapshot = json.loads(file.read_text(encoding='utf-8'))
    digest = snapshot.get('sha256', '')
    if len(digest) != 64 or any(c not in '0123456789abcdef' for c in digest):
        raise ValueError('Invalid cached provider checksum.')
    raw = (directory / f'{digest}.provider.json').read_bytes()
    if hashlib.sha256(raw).hexdigest() != digest:
        raise ValueError('Cached provider response failed its integrity check.')
    if snapshot.get('city') != city or snapshot.get('data_kind') != 'real':
        raise ValueError('Cached provider provenance is inconsistent.')
    snapshot['rows'] = normalize_response(json.loads(raw))
    return snapshot


def main():
    import argparse
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--city', choices=CITIES, default='Delhi')
    parser.add_argument('--output', default='artifacts/live')
    args = parser.parse_args()
    result = fetch_public_forecast(args.city, args.output)
    print(json.dumps({k: result[k] for k in ('city', 'retrieved_at', 'sha256', 'bytes')}, indent=2))


if __name__ == '__main__':
    main()
