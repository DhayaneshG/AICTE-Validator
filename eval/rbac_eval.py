"""RBAC / row-isolation matrix: every (user, request) pair has an EXPECTED status code; we send the real HTTP request and compare.

    python -m eval.rbac_eval [--json]

Builds a throw-away DB (8 synthetic applications), runs the matrix, prints the pass rate. `run_matrix` is also used by tests/test_security.py.
Expected codes encode the policy: 401 no/invalid token, 403 wrong role, 404 object outside the caller's institution/region.
"""
from __future__ import annotations

import json
import os
import shutil
import sys
import tempfile

PW = "Demo@1234"


def build_ctx(db):
    """Pick fixtures from the seeded DB: two institutions (inst.a / inst.b), an app A owned by inst.a, an app X in another region."""
    from backend.app import models as M
    users = {u.username: u for u in db.query(M.User).all()}
    a_inst, b_inst = users["inst.a"].institution_id, users["inst.b"].institution_id
    apps = db.query(M.Application).order_by(M.Application.id).all()
    A = next(x for x in apps if x.institution_id == a_inst)
    other_region = next((x for x in apps if x.region != A.region), None)
    B = next((x for x in apps if x.institution_id == b_inst), None)
    F = db.query(M.Finding).filter(M.Finding.application_id == A.id, M.Finding.active.is_(True), M.Finding.status == "open").first()
    D = db.query(M.Document).filter(M.Document.application_id == A.id, M.Document.is_current.is_(True)).first()
    return {"A": A.id, "A_region": A.region, "A_inst": a_inst, "B_inst": b_inst, "X": other_region.id if other_region else None,
            "X_region": other_region.region if other_region else None, "F": F.id if F else None, "D": D.id if D else None,
            "n_apps": len(apps), "n_apps_region_A": sum(1 for x in apps if x.region == A.region),
            "n_apps_inst_A": sum(1 for x in apps if x.institution_id == a_inst)}


def run_matrix(client, ctx) -> list[dict]:
    rA, rX = ctx["A_region"].lower(), ctx["X_region"].lower()
    A, F, D = ctx["A"], ctx["F"], ctx["D"]
    tokens: dict[str, dict] = {"anon": {}}

    def hdr(u):
        if u not in tokens:
            r = client.post("/api/auth/login", json={"username": u, "password": PW})
            tokens[u] = {"Authorization": f"Bearer {r.json()['access_token']}"}
        return tokens[u]

    results: list[dict] = []

    def case(name, user, method, url, expect, json_body=None):
        r = client.request(method, url, headers=hdr(user), json=json_body)
        results.append({"case": name, "user": user, "request": f"{method} {url}", "expected": expect, "got": r.status_code, "ok": r.status_code == expect})
        return r

    staff_in = [f"scrutiny.{rA}", f"regional.{rA}", "admin"]
    # --- authentication
    for url in ("/api/applications", f"/api/applications/{A}", f"/api/documents/{D}/file", "/api/audit", "/api/users"):
        case("unauthenticated rejected", "anon", "GET", url, 401)
    results.append({"case": "invalid token rejected", "user": "forged", "request": "GET /api/applications", "expected": 401,
                    "got": (g := client.get("/api/applications", headers={"Authorization": "Bearer not.a.jwt"}).status_code), "ok": g == 401})
    # --- application detail: row isolation
    case("own institution can read", "inst.a", "GET", f"/api/applications/{A}", 200)
    case("other institution denied", "inst.b", "GET", f"/api/applications/{A}", 404)
    for u in staff_in:
        case("officer in region can read", u, "GET", f"/api/applications/{A}", 200)
    case("officer of other region denied", f"scrutiny.{rX}", "GET", f"/api/applications/{A}", 404)
    case("regional officer of other region denied", f"regional.{rX}", "GET", f"/api/applications/{A}", 404)
    # --- documents (every view is audited elsewhere)
    for kind in ("file", "page/1.png"):
        case(f"document {kind}: own institution", "inst.a", "GET", f"/api/documents/{D}/{kind}", 200)
        case(f"document {kind}: other institution denied", "inst.b", "GET", f"/api/documents/{D}/{kind}", 404)
        case(f"document {kind}: officer of other region denied", f"scrutiny.{rX}", "GET", f"/api/documents/{D}/{kind}", 404)
        case(f"document {kind}: officer in region", f"scrutiny.{rA}", "GET", f"/api/documents/{D}/{kind}", 200)
    # --- role gates
    for u in ("inst.a", "inst.b"):
        case("institution user cannot read report", u, "GET", f"/api/applications/{A}/report", 403)
        case("institution user cannot run pipeline", u, "POST", f"/api/applications/{A}/run", 403)
        case("institution user cannot act on finding", u, "POST", f"/api/findings/{F}/actions", 403, {"action": "accept", "reason": "attempt"})
        case("institution user cannot read audit", u, "GET", "/api/audit", 403)
        case("institution user cannot retry verifications", u, "POST", "/api/verifications/retry", 403)
        case("institution user cannot list users", u, "GET", "/api/users", 403)
        case("institution user cannot purge", u, "POST", "/api/admin/purge?dry_run=true", 403)
    case("scrutiny officer cannot read audit", f"scrutiny.{rA}", "GET", "/api/audit", 403)
    case("regional officer cannot read audit", f"regional.{rA}", "GET", "/api/audit", 403)
    case("scrutiny officer cannot retry verifications", f"scrutiny.{rA}", "POST", "/api/verifications/retry", 403)
    case("scrutiny officer cannot purge", f"scrutiny.{rA}", "POST", "/api/admin/purge?dry_run=true", 403)
    case("regional officer cannot purge", f"regional.{rA}", "POST", "/api/admin/purge?dry_run=true", 403)
    case("regional officer can retry verifications", f"regional.{rA}", "POST", "/api/verifications/retry", 200)
    case("admin can read audit", "admin", "GET", "/api/audit", 200)
    case("admin can verify audit chain", "admin", "GET", "/api/audit/verify", 200)
    case("admin can list users", "admin", "GET", "/api/users", 200)
    case("admin can dry-run purge", "admin", "POST", "/api/admin/purge?dry_run=true", 200)
    # --- officers: report / run / finding action scoped to region
    case("officer in region reads report", f"scrutiny.{rA}", "GET", f"/api/applications/{A}/report", 200)
    case("officer of other region cannot read report", f"scrutiny.{rX}", "GET", f"/api/applications/{A}/report", 404)
    case("officer of other region cannot run", f"scrutiny.{rX}", "POST", f"/api/applications/{A}/run", 404)
    case("officer in region can run", f"scrutiny.{rA}", "POST", f"/api/applications/{A}/run", 200)
    if F:
        F2 = client.get(f"/api/applications/{A}/findings", headers=hdr("admin")).json()
        F = next((f["id"] for f in F2 if f["status"] == "open"), F)
        case("officer of other region cannot act on finding", f"scrutiny.{rX}", "POST", f"/api/findings/{F}/actions", 404, {"action": "accept", "reason": "attempt"})
        case("officer in region can act on finding", f"scrutiny.{rA}", "POST", f"/api/findings/{F}/actions", 201, {"action": "request_clarification", "reason": "rbac matrix"})
        case("other institution cannot read finding", "inst.b", "GET", f"/api/findings/{F}", 404)
        case("own institution can read finding", "inst.a", "GET", f"/api/findings/{F}", 200)
    # --- list endpoints return only in-scope rows
    def scoped(name, user, url, pred, key="items", expect_n=None):
        r = client.get(url, headers=hdr(user))
        rows = r.json().get(key, []) if isinstance(r.json(), dict) else r.json()
        ok = r.status_code == 200 and all(pred(x) for x in rows) and (expect_n is None or len(rows) == expect_n)
        results.append({"case": name, "user": user, "request": f"GET {url}", "expected": f"only in-scope rows ({len(rows)})", "got": r.status_code, "ok": ok})
    scoped("queue: institution user sees only own institution", "inst.a", "/api/applications", lambda i: i["institution_id"] == ctx["A_inst"], expect_n=ctx["n_apps_inst_A"])
    scoped("queue: scrutiny officer sees only own region", f"scrutiny.{rA}", "/api/applications", lambda i: i["region"] == ctx["A_region"], expect_n=ctx["n_apps_region_A"])
    scoped("queue: admin sees all", "admin", "/api/applications", lambda i: True, expect_n=ctx["n_apps"])
    scoped("findings: institution user sees only own institution", "inst.a", "/api/findings",
           lambda f: f["application_id"] in {i["id"] for i in client.get("/api/applications", headers=hdr("inst.a")).json()["items"]}, key=None)
    # --- create application scope
    case("institution user cannot create for another institution", "inst.a", "POST", "/api/applications", 403,
         {"institution_id": ctx["B_inst"], "category": "eoa_renewal_basic"})
    case("officer cannot create outside own region", f"scrutiny.{rX}", "POST", "/api/applications", 403,
         {"institution_id": ctx["A_inst"], "category": "eoa_renewal_basic"})
    return results


def summarise(results: list[dict]) -> dict:
    n_ok = sum(r["ok"] for r in results)
    return {"cases": len(results), "passed": n_ok, "pass_rate": n_ok / len(results) if results else 0.0, "failures": [r for r in results if not r["ok"]]}


def main(argv: list[str]) -> int:
    tmp = tempfile.mkdtemp(prefix="aicte_rbac_")
    os.environ.update({"AICTE_VAR": tmp, "AICTE_DB": f"sqlite:///{tmp}/rbac.db", "AICTE_SCHEDULER": "0", "AICTE_BACKOFF_BASE": "0.001"})
    from fastapi.testclient import TestClient

    from backend.app import seed
    from backend.app.db import SessionLocal, init_db
    from backend.app.main import app
    try:
        init_db()
        db = SessionLocal()
        seed.seed_institutions(db)
        db.commit()
        print("seeding 8 synthetic applications...", file=sys.stderr, flush=True)
        seed.seed_applications(db, limit=8)
        seed.seed_users(db)
        db.commit()
        ctx = build_ctx(db)
        db.close()
        with TestClient(app) as c:
            res = run_matrix(c, ctx)
        s = summarise(res)
        if "--json" in argv:
            print(json.dumps({"summary": s, "results": res}, indent=1, default=str))
        else:
            print(f"RBAC matrix: {s['passed']}/{s['cases']} passed ({s['pass_rate']:.1%})")
            for f in s["failures"]:
                print("  FAIL", f)
        return 0 if s["passed"] == s["cases"] else 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
