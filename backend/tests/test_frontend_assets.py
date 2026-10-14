import re
import shutil
import subprocess
from pathlib import Path

import pytest

FRONTEND = Path(__file__).resolve().parents[2] / "frontend"
JS_FILES = sorted((FRONTEND / "js").rglob("*.js"))


def test_index_served(client):
    r = client.get("/")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/html")
    assert 'id="app"' in r.text and 'type="module"' in r.text
    for nav in ("datasets", "lineage", "experiments", "verification", "audit"):
        assert f'data-nav="{nav}"' in r.text


def test_assets_referenced_by_index_are_served(client):
    html = client.get("/").text
    refs = re.findall(r'(?:href|src)="((?:css|js)/[^"]+)"', html)
    assert "css/style.css" in refs and "js/app.js" in refs
    for ref in refs:
        r = client.get(f"/{ref}")
        assert r.status_code == 200, ref


def test_every_js_module_served_as_javascript(client):
    assert JS_FILES, "no frontend JS found"
    for path in JS_FILES:
        rel = path.relative_to(FRONTEND).as_posix()
        r = client.get(f"/{rel}")
        assert r.status_code == 200, rel
        assert "javascript" in r.headers["content-type"], (rel, r.headers["content-type"])
    css = client.get("/css/style.css")
    assert css.status_code == 200 and css.headers["content-type"].startswith("text/css")


def test_relative_imports_resolve():
    """Every `import ... from "./x.js"` / import("./x.js") points at an existing file."""
    for path in JS_FILES:
        for spec in re.findall(r'(?:from\s+|import\()\s*"(\.[^"]+)"', path.read_text(encoding="utf-8")):
            assert (path.parent / spec).resolve().is_file(), f"{path.name}: {spec}"


@pytest.mark.skipif(shutil.which("node") is None, reason="node not installed")
@pytest.mark.parametrize("path", JS_FILES, ids=lambda p: p.relative_to(FRONTEND).as_posix())
def test_node_check(path):
    r = subprocess.run(["node", "--check", str(path)], capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
