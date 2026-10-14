import dataclasses


def test_dev_status(client):
    assert client.get("/api/dev/status").json() == {"demo_mode": True}
    client.app.state.settings = dataclasses.replace(client.app.state.settings, demo_mode=False)
    assert client.get("/api/dev/status").json() == {"demo_mode": False}
    assert client.post("/api/dev/tamper/1").status_code == 404


def test_all_pages_present():
    from pathlib import Path
    pages = Path(__file__).resolve().parents[2] / "frontend" / "js" / "pages"
    for name in ("lineage", "experiments", "experiment", "compare", "verification", "audit"):
        assert (pages / f"{name}.js").is_file(), name
    assert not (pages / "placeholder.js").exists()


def test_quiet_verify_writes_no_audit_event(client, upload, sample_csv):
    vid = upload("q", "s.csv", sample_csv).json()["versions"][0]["id"]
    before = len(client.get("/api/audit/events?limit=1000").json())
    r = client.get(f"/api/chain/verify/dataset_version/{vid}?quiet=true")
    assert r.status_code == 200 and r.json()["verdict"] == "NOT_REGISTERED"
    assert len(client.get("/api/audit/events?limit=1000").json()) == before
    client.get(f"/api/chain/verify/dataset_version/{vid}")
    assert len(client.get("/api/audit/events?limit=1000").json()) == before + 1


def test_dashboard_and_theme_assets():
    from pathlib import Path
    fe = Path(__file__).resolve().parents[2] / "frontend"
    assert (fe / "js" / "pages" / "dashboard.js").is_file() and (fe / "js" / "components" / "theme.js").is_file()
    html = (fe / "index.html").read_text(encoding="utf-8")
    assert 'id="theme-toggle"' in html and 'data-nav="dashboard"' in html and "dataorigin-theme" in html
    css = (fe / "css" / "style.css").read_text(encoding="utf-8")
    assert '[data-theme="dark"]' in css and "prefers-reduced-motion" in css


def test_lineage_nodes_never_get_a_css_transform():
    """SVG nodes are placed by their transform attribute; any CSS transform on .lnode overrides it and stacks
    every node at the top-left (regression from the glass restyle)."""
    import re
    from pathlib import Path
    css = (Path(__file__).resolve().parents[2] / "frontend" / "css" / "style.css").read_text(encoding="utf-8")
    rules = re.findall(r"([^{}]*\.lnode[^{}]*)\{([^}]*)\}", css)
    assert rules and not any("transform" in body for _, body in rules)
    anims = set(re.findall(r"\.lnode\s*\{[^}]*animation:\s*([\w-]+)", css))
    for name in anims:
        frames = re.search(r"@keyframes\s+%s\s*\{(.*?)\}\s*\}" % re.escape(name), css, re.S)
        assert frames and "transform" not in frames.group(1), name
