from copy import deepcopy

import pytest

from bharatblend import explainability as module
from bharatblend.explainability import GroqNarrator, build_evidence
from bharatblend.settings import groq_keys, local_settings


@pytest.fixture(autouse=True)
def reset_narrator_state():
    module._cache.clear()
    module._cooldown_until = 0


@pytest.fixture
def evidence():
    receipt = {
        'receipt_id': 'unit-evidence', 'data_kind': 'synthetic', 'track': 'interpretable',
        'target': {'location_id': 'delhi', 'variable': 'temperature'},
        'explanation': {'type': 'exact_additive_logit_decomposition', 'gate': 'additive'},
        'sources': [
            {'source_id': 'a', 'weight': 0.75, 'logit': 1.1, 'term_contributions': {'intercept': 0.5, 'lead.hours': 0.6}},
            {'source_id': 'b', 'weight': 0.25, 'logit': 0.0, 'term_contributions': {'intercept': 0.2, 'lead.hours': -0.2}},
        ],
    }
    return build_evidence(receipt, {'models': [{'id': 'a', 'name': 'Model A'}, {'id': 'b', 'name': 'Model B'}]})


def test_explanation_uses_signed_pairwise_terms_and_retains_provenance(evidence):
    a, b = evidence['sources']
    assert a['compared_with'] == 'b'
    assert sum(x['logit_difference'] for x in a['pairwise_differences']) == pytest.approx(1.1)
    assert sum(x['logit_difference'] for x in b['pairwise_differences']) == pytest.approx(-1.1)
    assert a['logit_reconstruction_error'] == pytest.approx(0)
    assert evidence['data_kind'] == 'synthetic'


class Response:
    def __init__(self, status=200, content='Evidence-based explanation', headers=None):
        self.status_code, self.content, self.headers = status, content, headers or {}
        self.closed = False

    def json(self):
        return {'choices': [{'message': {'content': self.content}}]}

    def close(self):
        self.closed = True


class Session:
    def __init__(self, responses):
        self.responses, self.calls = responses, []

    def post(self, url, **kwargs):
        self.calls.append((url, kwargs))
        return self.responses.pop(0)


def test_no_key_does_not_call_provider(evidence):
    session = Session([])
    result = GroqNarrator({}, session).explain(evidence)
    assert result['provider'] == 'deterministic'
    assert not session.calls
    assert '75.0%' in result['text']


def test_ordered_credential_failover_cache_and_no_key_disclosure(evidence):
    failed, success = Response(401), Response(content='server-key-two should not be echoed')
    session = Session([failed, success])
    narrator = GroqNarrator({'GROQ_API_KEYS': '["server-key-one", "server-key-two"]'}, session)
    result = narrator.explain(evidence)
    assert len(session.calls) == 2
    assert result['provider'] == 'groq'
    assert 'server-key' not in str(result)
    assert 'server-key' not in str(narrator.status())
    assert failed.closed and success.closed
    assert narrator.explain(evidence)['cached']
    assert len(session.calls) == 2
    assert 'server-key' not in str(session.calls[0][1]['json'])
    assert 'synthetic' in session.calls[0][1]['json']['messages'][1]['content']


def test_rate_limit_does_not_rotate_keys(evidence):
    response = Response(429, headers={'retry-after': '90'})
    session = Session([response])
    narrator = GroqNarrator({'GROQ_API_KEYS': 'key-one,key-two'}, session)
    assert narrator.explain(evidence)['status'] == 'local'
    assert narrator.explain(evidence)['status'] == 'local'
    assert len(session.calls) == 1
    assert response.closed


@pytest.mark.parametrize('status', [400, 500, 302])
def test_bad_provider_response_returns_local_evidence(evidence, status):
    session = Session([Response(status)])
    result = GroqNarrator({'GROQ_API_KEY': 'local-key'}, session).explain(evidence)
    assert result['provider'] == 'deterministic'
    assert result['receipt_id'] == evidence['receipt_id']


def test_non_additive_gate_does_not_fabricate_feature_reasons():
    result = build_evidence({'explanation': {'gate': 'spatial_attention'},
        'sources': [{'source_id': 'x', 'weight': 1, 'logit': 0}]}, {'models': []})
    assert not result['exact_logit_explanation']
    assert result['sources'][0]['pairwise_differences'] == []
    assert result['sources'][0]['logit_reconstruction_error'] is None


def test_configuration_is_nonexecuting_deduplicated_and_environment_wins(tmp_path, monkeypatch):
    file = tmp_path / '.env'
    file.write_text('GROQ_API_KEYS=["a","a","b"]\nGROQ_MODEL=file-model\nUNRELATED=$(do-not-run)\n')
    monkeypatch.delenv('GROQ_API_KEYS', raising=False)
    monkeypatch.delenv('GROQ_API_KEY', raising=False)
    monkeypatch.setenv('GROQ_MODEL', 'environment-model')
    config = local_settings(file)
    assert groq_keys(config) == ['a', 'b']
    assert config['GROQ_MODEL'] == 'environment-model'
    assert config['UNRELATED'] == '$(do-not-run)'


@pytest.mark.parametrize('raw', ['["secret",', '[1]', '[null]'])
def test_invalid_key_list_never_echoes_credentials(raw):
    with pytest.raises(ValueError) as exc:
        groq_keys({'GROQ_API_KEYS': raw})
    assert 'secret' not in str(exc.value)


def test_explanation_does_not_mutate_receipt(evidence):
    original = deepcopy(evidence)
    GroqNarrator({}).explain(evidence)
    assert evidence == original
