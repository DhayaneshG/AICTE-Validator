import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { ErrorBox, fmt } from './ui.jsx'

export default function Reports() {
  const [s, setS] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.summary().then(setS).catch(setErr) }, [])
  const dl = (p, n) => api.download(p, n).catch(setErr)
  const f = s?.findings
  return (
    <div>
      <h2>Reports</h2>
      <p className="muted">Exports use the same figures as the queue tiles. Each export is recorded in the audit trail. Findings are advisory indicators for human review.</p>
      <ErrorBox error={err} />
      <div className="filters">
        <button onClick={() => dl('/reports/findings.csv', 'findings.csv')}>Download findings (CSV)</button>
        <button onClick={() => dl('/reports/summary.pdf', 'validation-summary.pdf')}>Download summary (PDF)</button>
      </div>
      {s && (<>
        <p className="muted">Scope: {s.scope} &middot; generated {fmt(s.generated_at)}</p>
        <div className="tiles">
          {['high', 'medium', 'low'].map(k => <div key={k} className={`tile sev-${k}`}><div className="num">{f.open_by_severity[k]}</div><div>{k} open</div></div>)}
          <div className="tile"><div className="num">{f.manual_review}</div><div>manual review</div></div>
          <div className="tile"><div className="num">{s.applications.total}</div><div>applications</div></div>
        </div>
        <h3>By region</h3>
        <table className="queue"><thead><tr><th>Region</th><th>Applications</th><th>Open high</th><th>Open medium</th><th>Open low</th><th>Manual review</th></tr></thead>
          <tbody>{s.by_region.map(r => <tr key={r.region}><td>{r.region}</td><td>{r.applications}</td><td>{r.open_high}</td><td>{r.open_medium}</td><td>{r.open_low}</td><td>{r.manual_review}</td></tr>)}</tbody></table>
        <h3>Alerts</h3>
        <p>Open {s.alerts.open} &middot; acknowledged {s.alerts.acknowledged} &middot; resolved {s.alerts.resolved}</p>
      </>)}
    </div>
  )
}
