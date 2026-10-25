import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { fmt } from './ui.jsx'

const Row = ({ label, to, cta = 'Click Here' }) => (
  <div className="linkrow"><span className="lbl">{label}</span><a className="btn" href={`#${to}`}>{cta}</a></div>
)

function Card({ title, items, to }) {
  return (
    <div className="card3">
      <header>{title}</header>
      <ul>{items.length ? items.map((i, k) => <li key={k}>{i.text}<div><span className="pub">{i.when}</span></div></li>) : <li>Nothing to show yet.</li>}</ul>
      <div className="more"><a className="btn" href={`#${to}`}>Read More</a></div>
    </div>
  )
}

export default function Home({ user, meta }) {
  const staff = user.role !== 'institution_user'
  const [q, setQ] = useState(null)
  const [al, setAl] = useState(null)
  const [no, setNo] = useState(null)
  useEffect(() => {
    api.queue({}).then(setQ).catch(() => {})
    api.notifications({ limit: 5 }).then(setNo).catch(() => {})
    if (staff) api.alerts({ status: 'open' }).then(setAl).catch(() => {})
  }, [])
  const ticker = staff ? (al?.items || []).slice(0, 6).map(a => `${a.rule_id} - ${a.application_id}: ${a.message}`) : (no?.items || []).filter(n => !n.mock).slice(0, 6).map(n => n.subject)
  const apps = (q?.items || []).slice(0, 3).map(i => ({ text: `${i.id} - ${i.institution_name} (${i.counts.high} high, ${i.counts.medium} medium open)`, when: `Submitted ${i.submission_date}` }))
  const alerts = (al?.items || []).slice(0, 3).map(a => ({ text: `[${a.severity}] ${a.rule_id} - ${a.application_id}`, when: `Level: ${a.level.replace('_', ' ')}` }))
  const notes = (no?.items || []).filter(n => !n.mock).slice(0, 3).map(n => ({ text: n.subject, when: `Published ${fmt(n.created_at)}` }))
  const t = q?.totals
  return (
    <div>
      <div className="home">
        <div className="panel">
          <h2>Approval Process 2026-2027</h2>
          <p className="sub">Extension of Approval (EoA) scrutiny support &middot; per Approval Process Handbook 2024-27</p>
          <Row label={staff ? 'Scrutiny queue (prioritised applications)' : 'My applications'} to="/queue" />
          <Row label="Submit a new application and documents" to="/queue/new" cta="Start Here" />
          {staff && <Row label="Alerts &amp; escalation" to="/alerts" />}
          {staff && <Row label="Reports (CSV / PDF)" to="/reports" cta="To Download Click Here" />}
          <Row label="Approval Process Handbook (APH) cross-check :" to="/aph" cta="View APH Checklist" />
          <Row label="Notifications (in-app and MOCK e-mail) :" to="/notifications" />
          {user.role === 'admin_senior' && <Row label="Audit trail (hash-chained, append-only) :" to="/audit" />}
        </div>
        <div className="panel side">
          <div className="sbox"><span className="lbl">Open findings in your scope :</span>
            <div className="tiles" style={{ justifyContent: 'center' }}>
              {['high', 'medium', 'low'].map(s => <div key={s} className={`tile sev-${s}`}><div className="num">{t ? t[s] : '-'}</div><div>{s}</div></div>)}
              <div className="tile"><div className="num">{t ? t.manual_review : '-'}</div><div>manual review</div></div>
            </div>
          </div>
          <div className="sbox"><span className="lbl">Findings are advisory indicators for human review</span><a className="btn" href="#/aph">Click Here to Know More</a></div>
          <div className="sbox"><span className="lbl">All data is synthetic; MOCK sources are labelled</span>
            <div className="small muted">{(meta?.mock_components || []).map(c => c.name).join(' · ')}</div></div>
        </div>
      </div>
      <div className="ticker"><span className="tag">LATEST NEWS</span><div className="run"><span>{ticker.length ? ticker.join('   *   ') : 'No open alerts or notifications.'}</span></div></div>
      <div className="cards3">
        <Card title={staff ? 'ALERTS' : 'NOTIFICATIONS'} items={staff ? alerts : notes} to={staff ? '/alerts' : '/notifications'} />
        <Card title="PRIORITY APPLICATIONS" items={apps} to="/queue" />
        <Card title={staff ? 'NOTIFICATIONS' : 'ABOUT THIS PROTOTYPE'} items={staff ? notes : [{ text: 'Checks documents against the APH-based checklist and shows evidence for officers to review.', when: 'Advisory only' }]} to={staff ? '/notifications' : '/aph'} />
      </div>
    </div>
  )
}
