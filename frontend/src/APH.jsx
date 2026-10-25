import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { ErrorBox } from './ui.jsx'

const LBL = { covered: 'Covered', partial: 'Partially covered', not_covered: 'Not covered' }

export default function APH() {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.aphCrosscheck().then(setD).catch(setErr) }, [])
  const n = k => (d ? d.items.filter(i => i.coverage === k).length : 0)
  return (
    <div>
      <h2>Approval Process Handbook (APH) cross-check</h2>
      <p className="muted">{d?.source} {d?.note}</p>
      <ErrorBox error={err} />
      {d && <div className="tiles small">
        <div className="tile"><div className="num cov-covered">{n('covered')}</div><div>covered</div></div>
        <div className="tile"><div className="num cov-partial">{n('partial')}</div><div>partially covered</div></div>
        <div className="tile"><div className="num cov-not_covered">{n('not_covered')}</div><div>not covered (planned)</div></div>
      </div>}
      {d && <table className="aph"><thead><tr><th>APH reference</th><th>Requirement in the handbook</th><th>Coverage</th><th>What this validator does</th></tr></thead>
        <tbody>{d.items.map((i, k) => <tr key={k}><td>{i.aph}</td><td>{i.requirement}</td><td className={`cov cov-${i.coverage}`}>{LBL[i.coverage]}</td><td>{i.validator}</td></tr>)}</tbody></table>}
    </div>
  )
}
