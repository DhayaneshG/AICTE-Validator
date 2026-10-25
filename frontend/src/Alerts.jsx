import React, { useEffect, useState } from 'react'
import { api, session } from './api.js'
import { Empty, ErrorBox, Sev, fmt } from './ui.jsx'

const LEVEL = { scrutiny_officer: 'Scrutiny officer', regional_officer: 'Regional officer', admin_senior: 'Senior admin' }
const Level = ({ l }) => <span className={`badge lvl-${l}`}>{LEVEL[l] || l}</span>

export default function Alerts() {
  const [data, setData] = useState(null)
  const [f, setF] = useState({ status: '', level: '' })
  const [err, setErr] = useState(null)
  const [open, setOpen] = useState(null)
  const [msg, setMsg] = useState('')
  const load = () => api.alerts(f).then(setData).catch(setErr)
  useEffect(() => { load() }, [f])
  const ack = async id => { try { await api.ackAlert(id); load() } catch (x) { setErr(x) } }
  const run = async () => { try { const r = await api.evaluateAlerts(); setMsg(`Engine run: ${r.raised} raised, ${r.escalated} escalated, ${r.resolved} resolved`); load() } catch (x) { setErr(x) } }
  const canRun = ['regional_officer', 'admin_senior'].includes(session.user?.role)
  return (
    <div>
      <h2>Alerts &amp; escalation</h2>
      <p className="muted">Alerts are raised by configurable rules (<code>backend/config/alert_rules.json</code>) and escalate scrutiny officer &rarr; regional officer &rarr; senior admin if not acknowledged in time. They are advisory prompts for human review.</p>
      <div className="tiles small">
        {data && Object.entries(data.by_level).map(([l, n]) => <div key={l} className="tile"><div className="num">{n}</div><div>{LEVEL[l]} (open)</div></div>)}
      </div>
      <div className="filters">
        <select value={f.status} onChange={e => setF({ ...f, status: e.target.value })} aria-label="Status"><option value="">Any status</option>{['open', 'acknowledged', 'resolved'].map(s => <option key={s}>{s}</option>)}</select>
        <select value={f.level} onChange={e => setF({ ...f, level: e.target.value })} aria-label="Level"><option value="">Any level</option>{Object.entries(LEVEL).map(([k, v]) => <option key={k} value={k}>{v}</option>)}</select>
        {canRun && <button className="ghost" onClick={run}>Run alert engine now</button>}
        {msg && <span className="muted">{msg}</span>}
      </div>
      <ErrorBox error={err} />
      {data && !data.items.length && <Empty>No alerts match.</Empty>}
      {data && data.items.length > 0 && (
        <table className="queue">
          <thead><tr><th>Sev</th><th>Rule</th><th>Application</th><th>Message</th><th>Level</th><th>Status</th><th>Due since</th><th /></tr></thead>
          <tbody>
            {data.items.map(a => (
              <React.Fragment key={a.id}>
                <tr onClick={() => setOpen(open === a.id ? null : a.id)} style={{ cursor: 'pointer' }}>
                  <td><Sev s={a.severity} /></td><td title={a.title}>{a.rule_id}</td>
                  <td><a href={`#/applications/${a.application_id}`} onClick={e => e.stopPropagation()}>{a.application_id}</a><div className="muted small">{a.region}</div></td>
                  <td>{a.message}</td><td><Level l={a.level} /></td><td>{a.status}</td><td className="small">{fmt(a.due_at)}</td>
                  <td>{a.status === 'open' && <button className="ghost" onClick={e => { e.stopPropagation(); ack(a.id) }}>Acknowledge</button>}</td>
                </tr>
                {open === a.id && <tr><td colSpan={8}><ul className="history">{a.history.map((h, i) => <li key={i}><strong>{h.event}</strong> {h.from_level ? `${h.from_level} \u2192 ${h.to_level}` : h.level || h.by || ''} <span className="muted">{fmt(h.at)}</span></li>)}</ul></td></tr>}
              </React.Fragment>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
