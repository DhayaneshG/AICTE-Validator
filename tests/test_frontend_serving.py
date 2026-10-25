"""M5: the built React app is served at '/', SPA fallback works, API stays under /api."""
import os
import re
import tempfile


from fastapi.testclient import TestClient  # noqa: E402

from backend.app.main import app  # noqa: E402


def test_index_assets_and_fallback():
    with TestClient(app) as c:
        r = c.get("/")
        assert r.status_code == 200 and '<div id="root">' in r.text
        js = re.search(r'/assets/[^"]+\.js', r.text).group(0)
        assert c.get(js).status_code == 200
        assert c.get("/some/client/route").text == r.text
        assert c.get("/api/nope").status_code == 404
        assert c.get("/api/health").json()["status"] == "ok"
