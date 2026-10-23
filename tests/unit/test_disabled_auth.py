import httpx

from app.main import app, settings


async def test_demo_login_disabled_without_real_auth(monkeypatch):
    monkeypatch.setattr(settings, "demo_mode", False)
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://localhost:8000"
    ) as client:
        response = await client.post("/api/v1/demo/sessions", json={"actor": "alice"})
        assert response.status_code == 404
        assert response.json()["error"]["code"] == "not_found"
        assert "set-cookie" not in response.headers
