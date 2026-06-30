import pytest


@pytest.mark.asyncio
async def test_root_redirects_to_login(client):
    resp = await client.get("/", follow_redirects=False)
    assert resp.status_code == 307
    assert resp.headers["location"] == "/login"


@pytest.mark.asyncio
async def test_login_page_returns_html(client):
    resp = await client.get("/login")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "OSINT Platform" in resp.text
    assert "login-form" in resp.text


@pytest.mark.asyncio
async def test_dashboard_page_returns_html(client):
    resp = await client.get("/dashboard")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "upload-form" in resp.text


@pytest.mark.asyncio
async def test_report_page_returns_html(client):
    resp = await client.get("/report")
    assert resp.status_code == 200
    assert "text/html" in resp.headers["content-type"]
    assert "report-content" in resp.text


@pytest.mark.asyncio
async def test_static_css_accessible(client):
    resp = await client.get("/static/css/style.css")
    assert resp.status_code == 200
    assert "text/css" in resp.headers["content-type"]


@pytest.mark.asyncio
async def test_static_js_accessible(client):
    resp = await client.get("/static/js/api.js")
    assert resp.status_code == 200
    assert "javascript" in resp.headers["content-type"]
