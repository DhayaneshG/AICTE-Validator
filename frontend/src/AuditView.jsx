import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { ErrorBox, fmt } from './ui.jsx'

export default function AuditView() {
  const [data, setData] = useState(null)
  const [chain, setChain] = useState(null)
  const [f, setF] = useState({ action: '', actor_name: '' })
  const [err, setErr] = useState(null)
  useEffect(() => { api.audit({ ...f, limit: 200 }).then(setData).catch(setErr) }, [f])
  const verify = () => api.auditVerify().then(setChain).catch(setErr)
  return (
    <div>
      <h2>Audit trail</h2>
      <p className="muted">Append-only and hash-chained: each entry commits to the previous one, so edits or deletions are detectable.</p>
      <div className="filters">
        <input placeholder="Action (e.g. finding.action)" value={f.action} onChange={e => setF({ ...f, action: e.target.value })} aria-label="Action" />
        <input placeholder="Actor" value={f.actor_name} onChange={e => setF({ ...f, actor_name: e.target.value })} aria-label="Actor" />
        <button className="ghost" onClick={verify}>Verify chain integrity</button>
        {chain && <span className={chain.ok ? 'ok' : 'error'}>{chain.ok ? `Chain intact (${chain.checked} entries)` : `Broken at entry ${chain.broken_at}`}</span>}
      </div>
      <ErrorBox error={err} />
      {data && (
        <table className="compact"><thead><tr><th>#</th><th>Time (UTC)</th><th>Actor</th><th>Action</th><th>Entity</th><th>Details</th></tr></thead><tbody>
          {data.items.map(a => <tr key={a.id}><td>{a.id}</td><td>{fmt(a.ts)}</td><td>{a.actor}{a.role ? <span className="muted"> ({a.role})</span> : ''}</td><td>{a.action}</td><td>{a.entity_type} {a.entity_id}</td><td className="small">{JSON.stringify(a.details)}</td></tr>)}
        </tbody></table>
      )}
      {data && <p className="muted small">Showing {data.items.length} of {data.total}.</p>}
    </div>
  )
}
