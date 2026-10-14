def test_health(client):
    r = client.get("/api/health")
    assert r.status_code == 200
    assert r.json() == {"status": "ok", "db": "ok", "chain": "disabled"}


def test_frontend_served(client):
    r = client.get("/")
    assert r.status_code == 200
    assert "DataOrigin" in r.text


def test_unknown_api_route_is_json_404(client):
    r = client.get("/api/nope")
    assert r.status_code == 404
    assert "detail" in r.json()
