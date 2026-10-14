"""Experiment audit report (JSON + printable HTML) and the reproducibility score."""
import html

from fastapi import HTTPException
from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from app.config import Settings
from app.models import AuditEvent, DatasetVersion, Experiment, Transformation, utcnow_iso
from app.services import audit, chain, ml, quality
from app.services.hashing import canonical, sha256_text
from app.services.transforms import summarize_ops

ENV_KEYS = ("python", "sklearn", "pandas", "numpy")
TOP_FINDINGS = 5

# (key, label, points, warning when failed)
CHECKS = [
    ("dataset_hash", "Dataset sha present and local file matches", 20,
     "The dataset file no longer matches its recorded SHA-256 (or the hash is missing). Re-upload the original "
     "file or investigate the change before trusting these results."),
    ("seed", "Random seed recorded", 15,
     "No random seed was recorded, so the train/test split and model cannot be recreated exactly."),
    ("hyperparams", "Hyperparameters recorded", 10,
     "Hyperparameters were not recorded. Record the full model configuration so the run can be repeated."),
    ("env", "Environment recorded", 10,
     "The software environment (Python, scikit-learn, pandas, numpy versions) was not fully recorded."),
    ("lineage", "Version lineage complete", 10,
     "The version lineage is incomplete: a parent version or its transformation record is missing."),
    ("quality", "Quality score ≥ 70", 10,
     "The training data scored below 70 on quality. Review the quality findings and clean the data."),
    ("reproduced", "Reproduced with matching metrics", 15,
     "This experiment has not been successfully reproduced. Run Reproduce to confirm the results repeat."),
    ("on_chain", "Dataset and experiment registered on chain", 10,
     "The dataset version and/or experiment is not registered on chain. Register both to get tamper-evident "
     "timestamps."),
]


def ancestry(db: Session, version: DatasetVersion) -> tuple[list[DatasetVersion], bool]:
    """Root → version. Returns (versions, complete) where complete means every parent exists and each
    parent→child step has a transformation row."""
    chain_, complete, seen = [version], True, {version.id}
    current = version
    while current.parent_version_id is not None:
        parent = db.get(DatasetVersion, current.parent_version_id)
        if parent is None or parent.id in seen:
            complete = False
            break
        has_t = db.scalar(select(Transformation.id).where(
            Transformation.input_version_id == parent.id, Transformation.output_version_id == current.id))
        complete = complete and has_t is not None
        chain_.append(parent)
        seen.add(parent.id)
        current = parent
    return list(reversed(chain_)), complete


def _version_row(v: DatasetVersion) -> dict:
    return {"id": v.id, "version_no": v.version_no, "parent_version_id": v.parent_version_id, "sha256": v.sha256,
            "file_format": v.file_format, "rows": v.row_count, "cols": v.col_count, "note": v.note,
            "created_at": v.created_at}


def _transformations(db: Session, versions: list[DatasetVersion]) -> list[dict]:
    ids = [v.id for v in versions]
    rows = db.scalars(select(Transformation).where(Transformation.output_version_id.in_(ids))
                      .order_by(Transformation.id))
    return [{"input_version_id": t.input_version_id, "output_version_id": t.output_version_id,
             "ops": t.ops_json, "summary": summarize_ops(t.ops_json), "created_at": t.created_at} for t in rows]


def _reproductions(db: Session, exp: Experiment) -> list[dict]:
    """Runs reproduced from this experiment, plus the original if this run is itself a reproduction."""
    linked = list(db.scalars(select(Experiment).where(Experiment.reproduced_from_id == exp.id)
                             .order_by(Experiment.id)))
    if exp.reproduced_from_id is not None:
        original = db.get(Experiment, exp.reproduced_from_id)
        if original is not None:
            linked.insert(0, original)
    out = []
    for r in linked:
        match, diffs = (ml.metric_diffs(exp.metrics_json, r.metrics_json)
                        if exp.metrics_json and r.metrics_json else (False, {}))
        out.append({"id": r.id, "name": r.name, "relation": "original" if r.id == exp.reproduced_from_id
                    else "reproduction", "status": r.status, "created_at": r.created_at,
                    "match": match, "metric_diffs": diffs})
    return out


def _audit_trail(db: Session, dataset_id: int, version_ids: list[int], experiment_ids: list[int]) -> list[dict]:
    events = db.scalars(select(AuditEvent).where(or_(
        and_(AuditEvent.entity_type == "dataset", AuditEvent.entity_id == dataset_id),
        and_(AuditEvent.entity_type == "dataset_version", AuditEvent.entity_id.in_(version_ids)),
        and_(AuditEvent.entity_type == "experiment", AuditEvent.entity_id.in_(experiment_ids)),
    )).order_by(AuditEvent.id))
    return [{"id": e.id, "event_type": e.event_type, "entity_type": e.entity_type, "entity_id": e.entity_id,
             "payload": e.payload_json, "event_hash": e.event_hash, "created_at": e.created_at} for e in events]


def score_checks(passed: dict[str, bool]) -> dict:
    checks, warnings, score = [], [], 0
    for key, label, points, warning in CHECKS:
        ok = bool(passed.get(key))
        score += points if ok else 0
        checks.append({"key": key, "label": label, "points": points if ok else 0, "max_points": points,
                       "passed": ok})
        if not ok:
            warnings.append(warning)
    return {"score": score, "max": 100, "checks": checks, "warnings": warnings}


def build(db: Session, settings: Settings, experiment_id: int) -> dict:
    exp = db.get(Experiment, experiment_id)
    if exp is None:
        raise HTTPException(404, f"experiment {experiment_id} not found")
    version = exp.version
    dataset = version.dataset
    versions, lineage_complete = ancestry(db, version)

    q = quality.latest_report(db, version.id)
    quality_section = None if q is None else {
        "version_id": version.id, "score": q.score, "created_at": q.created_at,
        "summary": q.report_json.get("summary"), "top_findings": q.report_json.get("findings", [])[:TOP_FINDINGS],
    }

    v_verify = chain.verify(db, settings, "dataset_version", version.id, log=False)
    e_verify = (chain.verify(db, settings, "experiment", exp.id, log=False) if exp.status == "completed"
                else None)
    reproductions = _reproductions(db, exp)
    v_record = chain.latest_record(db, "dataset_version", version.id)
    e_record = chain.latest_record(db, "experiment", exp.id)

    passed = {
        "dataset_hash": bool(version.sha256) and v_verify["local_match"],
        "seed": exp.seed is not None,
        "hyperparams": isinstance(exp.hyperparams_json, dict) and bool(exp.hyperparams_json),
        "env": all((exp.env_json or {}).get(k) for k in ENV_KEYS),
        "lineage": lineage_complete,
        "quality": q is not None and q.score >= 70,
        "reproduced": any(r["status"] == "completed" and r["match"] for r in reproductions),
        "on_chain": bool(v_record and v_record.status == "registered"
                         and e_record and e_record.status == "registered"),
    }
    linked_ids = [exp.id] + [r["id"] for r in reproductions]

    report = {
        "report_type": "experiment_audit",
        "generated_at": utcnow_iso(),
        "dataset": {"id": dataset.id, "name": dataset.name, "description": dataset.description,
                    "source": dataset.source, "created_at": dataset.created_at, "version_id": version.id,
                    "ancestry": [_version_row(v) for v in versions], "lineage_complete": lineage_complete},
        "transformations": _transformations(db, versions),
        "quality": quality_section,
        "experiment": {
            "id": exp.id, "name": exp.name, "version_id": exp.version_id, "status": exp.status, "error": exp.error,
            "config": {"task": exp.task, "algorithm": exp.algorithm, "target_column": exp.target_column,
                       "hyperparams": exp.hyperparams_json, "seed": exp.seed, "test_size": exp.test_size},
            "metrics": exp.metrics_json, "env": exp.env_json, "model_sha256": exp.model_sha256,
            "commitment_sha256": exp.commitment_sha256, "duration_ms": exp.duration_ms,
            "reproduced_from_id": exp.reproduced_from_id, "created_at": exp.created_at,
        },
        "reproductions": reproductions,
        "verification": {"dataset_version": v_verify, "experiment": e_verify},
        "audit_trail": {"events": _audit_trail(db, dataset.id, [v.id for v in versions], linked_ids),
                        "chain_verify": audit.verify_chain(db)},
        "reproducibility": score_checks(passed),
    }
    # Hash over the content only (not the generation time), so an unchanged state gives the same hash.
    report["report_sha256"] = sha256_text(canonical({k: v for k, v in report.items() if k != "generated_at"}))
    return report


# --- HTML ------------------------------------------------------------------------------------------------------

class Safe(str):
    """Already-escaped HTML."""


def _e(value) -> str:
    return html.escape("—" if value is None or value == "" else str(value))


def _table(headers: list[str], rows: list[list]) -> str:
    if not rows:
        return '<p class="muted">None.</p>'
    head = "".join(f"<th>{_e(h)}</th>" for h in headers)
    body = "".join("<tr>" + "".join(f"<td>{c}</td>" for c in row) + "</tr>" for row in rows)
    return f"<table><thead><tr>{head}</tr></thead><tbody>{body}</tbody></table>"


def _kv(pairs: list[tuple[str, object]]) -> str:
    return _table(["Field", "Value"], [[_e(k), v if isinstance(v, Safe) else _e(v)] for k, v in pairs])


def _mono(value) -> Safe:
    return Safe(f"<code>{_e(value)}</code>")


def _badge(cls: str, text) -> str:
    return f'<span class="badge {cls}">{_e(text)}</span>'


VERDICT_CLASS = {"VERIFIED": "ok", "LOCAL_ONLY": "warn", "NOT_REGISTERED": "warn", "TAMPERED": "bad"}
SEVERITY_CLASS = {"critical": "bad", "warning": "warn", "info": "ok"}

# "Chain Link" logo (same mark as the web app's sidebar), inline so the report stays self-contained.
LOGO_SVG = (
    '<svg width="28" height="28" viewBox="0 0 64 64" aria-hidden="true"><defs><linearGradient id="lg" x1="0" y1="0" '
    'x2="1" y2="1"><stop offset="0" stop-color="#818CF8"/><stop offset="1" stop-color="#4338CA"/></linearGradient>'
    '</defs><rect x="2" y="2" width="60" height="60" rx="17" fill="url(#lg)"/><g fill="none" stroke="#fff" '
    'stroke-width="5.5"><rect x="10" y="25" width="27" height="14" rx="7" transform="rotate(-45 23.5 32)"/>'
    '<rect x="27" y="25" width="27" height="14" rx="7" transform="rotate(-45 40.5 32)"/></g></svg>'
)

CSS = """
.brand{display:flex;align-items:center;gap:10px;font-weight:700;font-size:15px;margin-bottom:14px;color:#312E81}
*{box-sizing:border-box}body{font:14px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif;color:#1a1a1a;
background:#fff;margin:0;padding:32px;max-width:1000px;margin:0 auto}h1{font-size:24px;margin:0 0 4px}
h2{font-size:17px;margin:28px 0 8px;padding-bottom:4px;border-bottom:2px solid #e5e5e5;break-after:avoid}
.muted{color:#666}table{width:100%;border-collapse:collapse;margin:6px 0 12px;font-size:13px}
th,td{text-align:left;vertical-align:top;padding:5px 8px;border-bottom:1px solid #e5e5e5}
th{background:#f6f6f6;font-weight:600}tr{break-inside:avoid}code{font:12px ui-monospace,Consolas,monospace;
word-break:break-all}.score{display:flex;gap:20px;align-items:center;padding:14px 18px;border:1px solid #ddd;
border-radius:8px;margin:16px 0}.score b{font-size:40px;line-height:1}.badge{display:inline-block;
padding:1px 8px;border-radius:10px;font-size:12px;font-weight:600}.ok{background:#e3f4e8;color:#17663a}
.warn{background:#fdf2dc;color:#8a5a00}.bad{background:#fbe4e4;color:#a11d1d}ul{margin:4px 0;padding-left:20px}
@media print{body{padding:0;max-width:none}h2{break-after:avoid}.score{border-color:#999}}
"""


def to_html(r: dict) -> str:
    ds, ex, rep = r["dataset"], r["experiment"], r["reproducibility"]
    score_cls = "ok" if rep["score"] >= 80 else "warn" if rep["score"] >= 50 else "bad"
    parts = [
        f'<div class="brand">{LOGO_SVG}<span>DataOrigin</span></div>',
        f"<h1>Audit report — {_e(ex['name'])}</h1>",
        f'<p class="muted">Experiment #{ex["id"]} · dataset {_e(ds["name"])} · generated {_e(r["generated_at"])}'
        f'<br>Report SHA-256: {_mono(r["report_sha256"])}</p>',
        f'<div class="score"><b class="{score_cls}">{rep["score"]}</b><div><strong>Reproducibility score</strong>'
        f' / {rep["max"]}<br><span class="muted">{sum(c["passed"] for c in rep["checks"])} of '
        f'{len(rep["checks"])} checks passed</span></div></div>',
        "<h2>Reproducibility</h2>",
        _table(["Check", "Result", "Points"], [
            [_e(c["label"]), _badge("ok" if c["passed"] else "bad", "pass" if c["passed"] else "fail"),
             f'{c["points"]} / {c["max_points"]}']
            for c in rep["checks"]]),
    ]
    if rep["warnings"]:
        parts.append("<p><strong>Missing-metadata warnings</strong></p><ul>"
                     + "".join(f"<li>{_e(w)}</li>" for w in rep["warnings"]) + "</ul>")

    parts += ["<h2>Dataset</h2>", _kv([("Name", ds["name"]), ("Description", ds["description"]),
                                      ("Source", ds["source"]), ("Created", ds["created_at"]),
                                      ("Lineage complete", "yes" if ds["lineage_complete"] else "no")]),
              "<p><strong>Version ancestry</strong></p>",
              _table(["Version", "SHA-256", "Rows × cols", "Note", "Created"], [
                  [f'v{v["version_no"]}' + (" (used)" if v["id"] == ds["version_id"] else ""), _mono(v["sha256"]),
                   f'{v["rows"]} × {v["cols"]}', _e(v["note"]), _e(v["created_at"])] for v in ds["ancestry"]])]

    parts += ["<h2>Transformations</h2>", _table(["From → to", "Ops", "Created"], [
        [f'#{t["input_version_id"]} → #{t["output_version_id"]}', _e(t["summary"]), _e(t["created_at"])]
        for t in r["transformations"]])]

    q = r["quality"]
    parts.append("<h2>Quality</h2>")
    if q is None:
        parts.append('<p class="muted">No quality report.</p>')
    else:
        parts += [f'<p>Score <strong>{q["score"]}</strong> / 100</p>',
                  _table(["Severity", "Check", "Message", "Suggestion"], [
                      [_badge(SEVERITY_CLASS.get(f["severity"], "warn"), f["severity"]), _e(f["check"]),
                       _e(f["message"]), _e(f["suggestion"])]
                      for f in q["top_findings"]])]

    cfg = ex["config"]
    parts += ["<h2>Experiment</h2>", _kv([
        ("Status", ex["status"]), ("Error", ex["error"]), ("Task", cfg["task"]), ("Algorithm", cfg["algorithm"]),
        ("Target column", cfg["target_column"]), ("Hyperparameters", _mono(canonical(cfg["hyperparams"]))),
        ("Seed", cfg["seed"]), ("Test size", cfg["test_size"]), ("Duration (ms)", ex["duration_ms"]),
        ("Model SHA-256", _mono(ex["model_sha256"])), ("Commitment SHA-256", _mono(ex["commitment_sha256"])),
        ("Reproduced from", ex["reproduced_from_id"]), ("Created", ex["created_at"])]),
        "<p><strong>Metrics</strong></p>", _kv(list((ex["metrics"] or {}).items())),
        "<p><strong>Environment</strong></p>", _kv(list((ex["env"] or {}).items()))]

    parts += ["<h2>Reproductions</h2>", _table(["Run", "Relation", "Status", "Metrics match", "Created"], [
        [f'#{x["id"]} {_e(x["name"])}', _e(x["relation"]), _e(x["status"]),
         _badge("ok" if x["match"] else "bad", "yes" if x["match"] else "no"),
         _e(x["created_at"])] for x in r["reproductions"]])]

    rows = []
    for label, v in (("Dataset version", r["verification"]["dataset_version"]),
                     ("Experiment", r["verification"]["experiment"])):
        if v is None:
            rows.append([_e(label), '<span class="muted">not applicable (run did not complete)</span>', "", "", ""])
            continue
        oc = v["on_chain"]
        rows.append([_e(label), _badge(VERDICT_CLASS.get(v["verdict"], "warn"), v["verdict"]), _mono(v["db_sha"]),
                     "yes" if v["local_match"] else "no",
                     _e("unavailable" if oc is None else
                        f'registered {oc["timestamp"]} tx {oc["tx_hash"]}' if oc["registered"] else "not on chain")])
    parts += ["<h2>Verification</h2>", _table(["Artifact", "Verdict", "Recorded SHA-256", "Local match", "On chain"],
                                              rows)]

    trail = r["audit_trail"]
    cv = trail["chain_verify"]
    parts += ["<h2>Audit trail</h2>",
              f'<p>Audit hash chain: <span class="badge {"ok" if cv["valid"] else "bad"}">'
              f'{"valid" if cv["valid"] else "BROKEN at event " + str(cv["broken_at"])}</span> '
              f'<span class="muted">({cv["checked"]} events checked)</span></p>',
              _table(["#", "Event", "Entity", "Time", "Hash"], [
                  [str(e["id"]), _e(e["event_type"]), f'{_e(e["entity_type"])} #{e["entity_id"]}', _e(e["created_at"]),
                   _mono(e["event_hash"][:16] + "…")] for e in trail["events"]])]

    return (f'<!doctype html><html lang="en"><head><meta charset="utf-8">'
            f'<meta name="viewport" content="width=device-width,initial-scale=1">'
            f'<title>Audit report — {_e(ex["name"])}</title><style>{CSS}</style></head>'
            f'<body>{"".join(parts)}</body></html>')
