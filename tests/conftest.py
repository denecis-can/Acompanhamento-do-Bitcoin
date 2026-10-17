import json
from pathlib import Path

import pytest

from app.core.http import HttpClient

FIXTURES = Path(__file__).parent / "fixtures"


def load(name: str):
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


@pytest.fixture
async def http():
    # sem backoff nem throttle: os testes de retry não precisam esperar
    client = HttpClient(timeout=2, max_retries=2, backoff_base=0, throttle=False)
    yield client
    await client.aclose()
