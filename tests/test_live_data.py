from copy import deepcopy
import hashlib
import json

import pytest

from bharatblend.live_data import FIELDS, LIVE_MODELS, fetch_public_forecast, load_snapshot, normalize_response


@pytest.fixture
def response_data():
    data = {'utc_offset_seconds': 0, 'hourly': {'time': ['2026-09-26T12:00', '2026-09-26T13:00']}, 'hourly_units': {}}
    values = {'temperature_2m': [30, None], 'precipitation': [0, 2],
              'wind_speed_10m': [10, 10], 'wind_direction_10m': [0, 90]}
    for model in LIVE_MODELS:
        for field, unit in FIELDS.items():
            data['hourly'][f'{field}_{model}'] = values[field].copy()
            data['hourly_units'][f'{field}_{model}'] = unit
    return data


def test_actual_intervals_units_missingness_and_wind_conversion(response_data):
    rows = normalize_response(response_data)
    assert len(rows) == 6
    assert rows[0]['precipitation_start'] == '2026-09-26T11:00:00+00:00'
    assert rows[1]['temperature_2m'] is None
    assert rows[0]['v10'] == pytest.approx(-10)
    assert rows[1]['u10'] == pytest.approx(-10)


@pytest.mark.parametrize('change', ['wrong_unit', 'absent_model', 'short_field', 'infinite', 'duplicate_time', 'negative_rain'])
def test_bad_provider_contract_is_rejected(response_data, change):
    data = deepcopy(response_data)
    if change == 'wrong_unit':
        data['hourly_units']['temperature_2m_gfs_global'] = 'K'
    elif change == 'absent_model':
        del data['hourly']['temperature_2m_ecmwf_ifs025']
    elif change == 'short_field':
        data['hourly']['temperature_2m_gfs_global'] = [30]
    elif change == 'infinite':
        data['hourly']['temperature_2m_gfs_global'][0] = float('inf')
    elif change == 'duplicate_time':
        data['hourly']['time'][1] = data['hourly']['time'][0]
    else:
        data['hourly']['precipitation_gfs_global'][0] = -1
    with pytest.raises(ValueError):
        normalize_response(data)


class Response:
    status_code = 200
    headers = {}
    url = 'https://api.open-meteo.com/v1/forecast?models=gfs_global'

    def __init__(self, raw):
        self.raw, self.closed = raw, False

    def raise_for_status(self):
        pass

    def iter_content(self, size):
        yield self.raw

    def close(self):
        self.closed = True


class Session:
    def __init__(self, response):
        self.response = response

    def get(self, *args, **kwargs):
        return self.response


def test_download_retains_raw_provider_bytes_and_detects_corruption(response_data, tmp_path):
    raw = json.dumps(response_data).encode()
    response = Response(raw)
    receipt = fetch_public_forecast('Delhi', tmp_path, Session(response))
    assert receipt['data_kind'] == 'real'
    assert receipt['sha256'] == hashlib.sha256(raw).hexdigest()
    assert not receipt['trained_blend_applied']
    assert not receipt['skill_verified']
    assert receipt['model_run_initialization'] is None
    assert response.closed
    assert load_snapshot('Delhi', tmp_path)['rows'] == receipt['rows']
    (tmp_path / receipt['raw_file']).write_bytes(b'corrupt')
    with pytest.raises(ValueError, match='integrity'):
        load_snapshot('Delhi', tmp_path)


def test_response_budget_enforced_before_write(tmp_path):
    response = Response(b'x' * 2_000_001)
    with pytest.raises(ValueError, match='budget'):
        fetch_public_forecast('Delhi', tmp_path, Session(response))
    assert list(tmp_path.iterdir()) == []
    assert response.closed


def test_unavailable_city_does_not_fetch_or_traverse(tmp_path):
    with pytest.raises(ValueError):
        fetch_public_forecast('../private', tmp_path, Session(None))
    with pytest.raises(ValueError):
        load_snapshot('../private', tmp_path)
