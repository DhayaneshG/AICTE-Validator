"""Alert timeliness on a SIMULATED clock.

    python -m eval.alert_eval [--days 9] [--tick-minutes 5] [--json]

Seeds 12 synthetic applications at a simulated T0, then advances services.clock in scheduler-sized ticks, calling the alert engine each
tick (exactly what the 5-minute scheduler job does) while scripted reviewer events happen. Measures, per rule: detection lag
(alert created - alert due), escalation delay (escalated - scheduled escalation time), acknowledged-alert escalations (must be 0),
duplicate alerts (must be 0) and resolution lag after a reviewer decision. Time is simulated; the per-tick compute time is real.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import shutil
import statistics as st
import sys
import tempfile
import time

T0 = dt.datetime(2026, 3, 2, 9, 0, tzinfo=dt.timezone.utc)


def _aware(d):
    return d if d.tzinfo else d.replace(tzinfo=dt.timezone.utc)


def main(argv: list[str]) -> int:
    days = float(argv[argv.index("--days") + 1]) if "--days" in argv else 9.0
    tick = float(argv[argv.index("--tick-minutes") + 1]) if "--tick-minutes" in argv else 5.0
    tmp = tempfile.mkdtemp(prefix="aicte_alert_")
    os.environ.update({"AICTE_VAR": tmp, "AICTE_DB": f"sqlite:///{tmp}/a.db", "AICTE_SCHEDULER": "0", "AICTE_BACKOFF_BASE": "0.001"})
    from sqlalchemy import func, select

    from backend.app import models as M
    from backend.app import seed
    from backend.app.db import SessionLocal, init_db
    from backend.services import alerts as A
    from backend.services import processing as P
    from backend.services.clock import clock
    try:
        init_db()
        db = SessionLocal()
        seed.seed_institutions(db)
        db.commit()
        print("seeding 12 applications at simulated T0...", file=sys.stderr, flush=True)
        clock.set(T0)
        seed.seed_applications(db, limit=12)
        db.commit()
        cfg = A.load_rules()
        thr = {r["id"]: r for r in cfg["rules"]}

        # inject an unresolved verification streak (MOCK issuer outage) on one document so AL-VERIF-STUCK is exercised
        vdoc = db.execute(select(M.Verification).where(M.Verification.document_id.isnot(None))).scalars().first()
        if vdoc is not None:
            db.add(M.Verification(application_id=vdoc.application_id, document_id=vdoc.document_id, ref_id=vdoc.ref_id, doc_type=vdoc.doc_type,
                                  status="source_unavailable", reason="source_unavailable_after_retries", created_at=T0 + dt.timedelta(minutes=1)))
            db.commit()
        highs = db.execute(select(M.Finding).where(M.Finding.active.is_(True), M.Finding.status == "open", M.Finding.severity == "high").order_by(M.Finding.id)).scalars().all()
        meds = db.execute(select(M.Finding).where(M.Finding.active.is_(True), M.Finding.status == "open", M.Finding.severity == "medium").order_by(M.Finding.id)).scalars().all()
        script = []  # (simulated time, kind, target)
        if len(highs) > 0:
            script.append((T0 + dt.timedelta(hours=6), "acknowledge_high", highs[0].id))
        if len(highs) > 1:
            script.append((T0 + dt.timedelta(hours=10), "accept", highs[1].id))
        if len(meds) > 0:
            script.append((T0 + dt.timedelta(hours=30), "request_clarification", meds[0].id))
        script.sort()
        action_at: dict[str, dt.datetime] = {}
        acked_at: dict[int, dt.datetime] = {}

        end = T0 + dt.timedelta(days=days)
        now = T0
        n_ticks, compute = 0, []
        step = dt.timedelta(minutes=tick)
        total = int((end - T0) / step)
        while now <= end:
            clock.set(now)
            while script and script[0][0] <= now:
                _, kind, tgt = script.pop(0)
                if kind == "acknowledge_high":
                    al = db.execute(select(M.Alert).where(M.Alert.finding_id == tgt, M.Alert.rule_id == "AL-HIGH-OPEN")).scalar_one_or_none()
                    if al is not None:
                        A.acknowledge(db, al, "scrutiny.eval", "scrutiny_officer")
                        acked_at[al.id] = now
                else:
                    f = db.get(M.Finding, tgt)
                    P.apply_action(db, f, kind, "scripted simulation event", "scrutiny.eval", "scrutiny_officer")
                    action_at[tgt] = now
                db.commit()
            t = time.perf_counter()
            A.evaluate(db, now, cfg)
            db.commit()
            compute.append(time.perf_counter() - t)
            n_ticks += 1
            if n_ticks % 250 == 0:
                print(f"  tick {n_ticks}/{total} (simulated {now:%Y-%m-%d %H:%M}) alerts so far: {db.query(M.Alert).count()}", file=sys.stderr, flush=True)
            now += step

        alerts = db.execute(select(M.Alert)).scalars().all()
        per_rule: dict[str, dict] = {}
        esc_delays: list[float] = []
        esc_after_ack = 0
        for a in alerts:
            r = per_rule.setdefault(a.rule_id, {"alerts": 0, "lags": [], "esc": [], "resolved": 0})
            r["alerts"] += 1
            r["lags"].append((_aware(a.created_at) - _aware(a.due_at)).total_seconds() / 60)
            r["resolved"] += a.status == "resolved"
            levels = cfg["levels"]
            for ev in a.details.get("history", []):
                if ev["event"] == "escalated":
                    k = levels.index(ev["to_level"])
                    expected = _aware(a.due_at) + dt.timedelta(hours=thr[a.rule_id]["escalate_after_hours"][k - 1])
                    d = (dt.datetime.fromisoformat(ev["at"]) - expected).total_seconds() / 60
                    r["esc"].append(d)
                    esc_delays.append(d)
                    if a.id in acked_at and dt.datetime.fromisoformat(ev["at"]) > acked_at[a.id]:
                        esc_after_ack += 1
        dups = db.execute(select(func.count()).select_from(select(M.Alert.dedup_key).group_by(M.Alert.dedup_key).having(func.count() > 1).subquery())).scalar_one()
        res_lags = []
        for fid, t_act in action_at.items():
            for a in alerts:
                if a.finding_id == fid and a.status == "resolved" and a.resolved_at:
                    res_lags.append((_aware(a.resolved_at) - t_act).total_seconds() / 60)

        def agg(v):
            return {"n": len(v), "mean_min": round(st.mean(v), 2) if v else None, "max_min": round(max(v), 2) if v else None}
        out = {"simulated": True, "tick_minutes": tick, "days": days, "ticks": n_ticks, "alerts": len(alerts),
               "mean_engine_ms_per_tick": round(1000 * st.mean(compute), 1), "max_engine_ms_per_tick": round(1000 * max(compute), 1),
               "per_rule": {k: {"alerts": v["alerts"], "resolved": v["resolved"], "detection_lag": agg(v["lags"]), "escalation_delay": agg(v["esc"])} for k, v in sorted(per_rule.items())},
               "detection_lag_all": agg([x for v in per_rule.values() for x in v["lags"]]), "escalation_delay_all": agg(esc_delays),
               "escalations_after_acknowledgement": esc_after_ack, "duplicate_dedup_keys": dups,
               "resolution_lag_after_decision": agg(res_lags),
               "within_one_tick": {"detections": sum(1 for v in per_rule.values() for x in v["lags"] if x <= tick), "escalations": sum(1 for x in esc_delays if x <= tick)},
               "notifications": {ch: n for ch, n in db.execute(select(M.Notification.channel, func.count()).group_by(M.Notification.channel)).all()},
               "audit_chain_ok": __import__("backend.services.audit", fromlist=["x"]).verify_chain(db)["ok"]}
        db.close()
        if "--json" in argv:
            print(json.dumps(out, indent=1))
        else:
            print(f"Alert timeliness (SIMULATED clock; {days:g} days, tick {tick:g} min, {n_ticks} ticks, {out['alerts']} alerts)")
            for k, v in out["per_rule"].items():
                print(f"  {k:18s} n={v['alerts']:3d} resolved={v['resolved']:3d} detect lag mean/max {v['detection_lag']['mean_min']}/{v['detection_lag']['max_min']} min; "
                      f"escalations {v['escalation_delay']['n']} (max delay {v['escalation_delay']['max_min']} min)")
            print(f"  duplicates={out['duplicate_dedup_keys']}  escalations after ack={esc_after_ack}  audit chain ok={out['audit_chain_ok']}")
        return 0
    finally:
        clock.reset()
        shutil.rmtree(tmp, ignore_errors=True)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
