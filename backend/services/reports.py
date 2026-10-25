"""Dashboard summary + CSV / PDF exports. All figures come from the same scoped queries, so CSV rows, the PDF tables and the
dashboard tiles reconcile (tested). Advisory framing and MOCK labels are printed on every export."""
from __future__ import annotations

import csv
import io

from reportlab.lib import colors
from reportlab.lib.pagesizes import A4
from reportlab.lib.styles import getSampleStyleSheet
from reportlab.lib.units import mm
from reportlab.platypus import Paragraph, SimpleDocTemplate, Spacer, Table, TableStyle
from sqlalchemy import select
from sqlalchemy.orm import Session

from ..app import models as M
from ..app.auth import Actor, scope_applications
from ..pipeline.types import ADVISORY
from .clock import utcnow

SEVS = ("high", "medium", "low")
CSV_COLS = ["application_id", "institution_id", "institution_name", "region", "category", "application_status", "finding_id", "rule_id", "severity",
            "source", "status", "page", "confidence", "message", "evidence"]


def _scope_label(actor: Actor) -> str:
    return {"admin_senior": "all regions", "institution_user": f"institution {actor.institution_id}"}.get(actor.role, f"region {actor.region}")


def scoped_findings(db: Session, actor: Actor):
    q = scope_applications(select(M.Finding, M.Application).join(M.Application, M.Application.id == M.Finding.application_id).where(M.Finding.active.is_(True)), actor)
    return db.execute(q.order_by(M.Application.id, M.Finding.id)).all()


def summary(db: Session, actor: Actor) -> dict:
    apps = db.execute(scope_applications(select(M.Application), actor)).scalars().all()
    rows = scoped_findings(db, actor)
    sev_status = {s: {} for s in SEVS}
    for f, _ in rows:
        sev_status[f.severity][f.status] = sev_status[f.severity].get(f.status, 0) + 1
    by_status: dict[str, int] = {}
    for a in apps:
        by_status[a.status] = by_status.get(a.status, 0) + 1
    regions: dict[str, dict] = {}
    for f, a in rows:
        r = regions.setdefault(a.region, {"region": a.region, "applications": 0, "open_high": 0, "open_medium": 0, "open_low": 0, "manual_review": 0})
        if f.status == "open":
            r[f"open_{f.severity}"] += 1
        elif f.status == "manual_review":
            r["manual_review"] += 1
    for a in apps:
        regions.setdefault(a.region, {"region": a.region, "applications": 0, "open_high": 0, "open_medium": 0, "open_low": 0, "manual_review": 0})["applications"] += 1
    alerts = db.execute(scope_applications(select(M.Alert).join(M.Application, M.Application.id == M.Alert.application_id), actor)).scalars().all()
    return {"generated_at": utcnow().isoformat(timespec="seconds"), "scope": _scope_label(actor), "advisory": ADVISORY,
            "applications": {"total": len(apps), "by_status": by_status},
            "findings": {"active_total": len(rows), "by_severity_status": sev_status,
                         "open_by_severity": {s: sev_status[s].get("open", 0) for s in SEVS},
                         "manual_review": sum(v.get("manual_review", 0) for v in sev_status.values())},
            "alerts": {"open": sum(a.status == "open" for a in alerts), "acknowledged": sum(a.status == "acknowledged" for a in alerts),
                       "resolved": sum(a.status == "resolved" for a in alerts),
                       "open_by_level": {lv: sum(1 for a in alerts if a.status != "resolved" and a.level == lv) for lv in ("scrutiny_officer", "regional_officer", "admin_senior")}},
            "by_region": sorted(regions.values(), key=lambda r: r["region"])}


def findings_csv(db: Session, actor: Actor) -> str:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(CSV_COLS)
    for f, a in scoped_findings(db, actor):
        w.writerow([a.id, a.institution_id, a.institution.name, a.region, a.category, a.status, f.id, f.rule_id, f.severity, f.source, f.status,
                    f.page or "", f"{f.confidence:.2f}", f.message, (f.evidence_text or "").replace("\n", " ")])
    return buf.getvalue()


def _table(data, widths=None, head=True):
    t = Table(data, colWidths=widths, repeatRows=1 if head else 0)
    st = [("FONTSIZE", (0, 0), (-1, -1), 8.5), ("GRID", (0, 0), (-1, -1), 0.25, colors.grey), ("VALIGN", (0, 0), (-1, -1), "TOP")]
    if head:
        st += [("BACKGROUND", (0, 0), (-1, 0), colors.HexColor("#e8ecf3")), ("FONTNAME", (0, 0), (-1, 0), "Helvetica-Bold")]
    t.setStyle(TableStyle(st))
    return t


def _doc(buf, title):
    return SimpleDocTemplate(buf, pagesize=A4, leftMargin=15 * mm, rightMargin=15 * mm, topMargin=15 * mm, bottomMargin=15 * mm, title=title, author="AICTE EoA Validator (MVP, synthetic data)")


def _head(story, ss, title, sub):
    story += [Paragraph(title, ss["Title"]), Paragraph(sub, ss["Normal"]), Spacer(1, 3 * mm),
              Paragraph(f"<b>Advisory only.</b> {ADVISORY} All data in this build is synthetic; sources marked MOCK are simulated.", ss["Italic"]), Spacer(1, 4 * mm)]


def summary_pdf(db: Session, actor: Actor) -> bytes:
    s = summary(db, actor)
    ss = getSampleStyleSheet()
    story: list = []
    _head(story, ss, "Validation summary report", f"Scope: {s['scope']} &middot; generated {s['generated_at']} UTC")
    story += [Paragraph("Applications", ss["Heading3"]), _table([["Total", "Status", "Count"], [s["applications"]["total"], "", ""]] + [["", k, v] for k, v in sorted(s["applications"]["by_status"].items())])]
    f = s["findings"]
    story += [Spacer(1, 4 * mm), Paragraph("Findings (active)", ss["Heading3"]),
              _table([["Severity", "Open", "Manual review", "Clarification requested", "Accepted", "Overridden", "Total"]] +
                     [[sv.capitalize()] + [f["by_severity_status"][sv].get(k, 0) for k in ("open", "manual_review", "clarification_requested", "accepted", "overridden")] + [sum(f["by_severity_status"][sv].values())] for sv in SEVS]
                     + [["All", sum(f["open_by_severity"].values()), f["manual_review"], "", "", "", f["active_total"]]]),
              Spacer(1, 2 * mm), Paragraph(f"Open findings by severity: high {f['open_by_severity']['high']}, medium {f['open_by_severity']['medium']}, low {f['open_by_severity']['low']}; manual review {f['manual_review']}.", ss["Normal"])]
    story += [Spacer(1, 4 * mm), Paragraph("By region", ss["Heading3"]),
              _table([["Region", "Applications", "Open high", "Open medium", "Open low", "Manual review"]] + [[r["region"], r["applications"], r["open_high"], r["open_medium"], r["open_low"], r["manual_review"]] for r in s["by_region"]])]
    al = s["alerts"]
    story += [Spacer(1, 4 * mm), Paragraph("Alerts", ss["Heading3"]),
              Paragraph(f"Open {al['open']}, acknowledged {al['acknowledged']}, resolved {al['resolved']}. Open/acknowledged by escalation level: " + ", ".join(f"{k.replace('_', ' ')} {v}" for k, v in al["open_by_level"].items()) + ".", ss["Normal"])]
    buf = io.BytesIO()
    _doc(buf, "Validation summary report").build(story)
    return buf.getvalue()


def application_pdf(db: Session, app: M.Application) -> bytes:
    from ..app.routes.reports import scrutiny_history
    ss = getSampleStyleSheet()
    small = ss["BodyText"].clone("small", fontSize=8, leading=10)
    story: list = []
    _head(story, ss, f"Validation report - {app.id}", f"{app.institution.name} ({app.region}) &middot; {app.category} &middot; status {app.status} &middot; generated {utcnow().isoformat(timespec='seconds')} UTC")
    docs = [d for d in app.documents if d.is_current]
    story += [Paragraph("Documents", ss["Heading3"]), _table([["Type", "File", "Ver.", "Source", "SHA-256 (first 16)"]] + [[d.doc_type or "?", d.filename, d.version, d.source or "", d.sha256[:16]] for d in docs])]
    fs = db.execute(select(M.Finding).where(M.Finding.application_id == app.id, M.Finding.active.is_(True)).order_by(M.Finding.id)).scalars().all()
    story += [Spacer(1, 4 * mm), Paragraph(f"Findings ({len(fs)})", ss["Heading3"])]
    story += [_table([["Severity", "Status", "Rule", "Finding / evidence"]] + [[f.severity, f.status, f.rule_id, Paragraph(f"{f.message}<br/><i>{(f.evidence_text or '')[:300]}</i>", small)] for f in fs], widths=[18 * mm, 28 * mm, 30 * mm, 104 * mm])] if fs else [Paragraph("No findings.", ss["Normal"])]
    vs = db.execute(select(M.Verification).where(M.Verification.application_id == app.id).order_by(M.Verification.id)).scalars().all()
    story += [Spacer(1, 4 * mm), Paragraph("Issuer verifications (MOCK issuer registry)", ss["Heading3"])]
    story += [_table([["Doc", "Reference", "Status", "Reason", "Attempts"]] + [[v.doc_type, v.ref_id or "", v.status, v.reason or "", len(v.attempts or [])] for v in vs])] if vs else [Paragraph("None.", ss["Normal"])]
    h = scrutiny_history(app.institution_id)
    story += [Spacer(1, 4 * mm), Paragraph("Scrutiny history (MOCK, synthetic)", ss["Heading3"])]
    story += [_table([["Year", "Outcome", "Deficiencies noted"]] + [[e.get("year", ""), e.get("outcome", ""), e.get("deficiencies_noted", "")] for e in h["entries"]])] if h["entries"] else [Paragraph("No history.", ss["Normal"])]
    buf = io.BytesIO()
    _doc(buf, f"Validation report {app.id}").build(story)
    return buf.getvalue()
