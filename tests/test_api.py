import sys
from pathlib import Path

from fastapi.testclient import TestClient

ROOT_DIR = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT_DIR))

from api.app import app  # noqa: E402


client = TestClient(app)


def test_health_returns_ok():
    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_shows_returns_list():
    response = client.get("/shows")

    assert response.status_code == 200
    shows = response.json()
    assert isinstance(shows, list)
    assert len(shows) == 2
    assert {"event_id", "event_name", "artist_name", "show_date"} <= set(shows[0])


def test_show_returns_matching_record():
    response = client.get("/show/demo_001")

    assert response.status_code == 200
    show = response.json()
    assert show["event_id"] == "demo_001"
    assert show["artist_name"] == "Fred again.."


def test_show_returns_404_for_missing_event():
    response = client.get("/show/does-not-exist")

    assert response.status_code == 404
    assert response.json()["detail"] == "Show not found: does-not-exist"
