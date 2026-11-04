"""Portable checks using fresh temporary state, without a supplier login or hardware."""
import hashlib
import json
from pathlib import Path

from fastapi.testclient import TestClient

from formforge.app import create_app
from formforge.config import Config
from formforge.adapters.firmware_targets import catalog

ROOT = Path(__file__).resolve().parents[1]


def test_resource_provenance():
    count = 0
    for manifest in (ROOT / 'docs/migrations').glob('*.json'):
        for row in json.loads(manifest.read_text('utf8')).get('files', []):
            path = row.get('destination', '')
            if path.startswith(('vendor/mechanical/', 'vendor/firmware/')):
                assert hashlib.sha256((ROOT / path).read_bytes()).hexdigest() == row['sha256']
                count += 1
    assert count > 60


def test_stm32_catalog():
    targets = catalog()
    assert targets
    assert len(targets) == 8


def test_fresh_http_service(tmp_path):
    assert Path(create_app.__code__.co_filename).resolve().is_relative_to(ROOT)
    config = Config(tmp_path)
    app = create_app(config)
    with TestClient(app, base_url='http://127.0.0.1:17840') as client:
        assert client.get('/health').status_code == 200
        assert client.get('/api/projects').status_code == 403
        response = client.get('/api/projects', headers={'Authorization': 'Bearer ' + config.token})
        assert response.status_code == 200
        assert response.json() == []
    assert (tmp_path / '.local/api-token.txt').is_file()
