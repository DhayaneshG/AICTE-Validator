import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { Empty, ErrorBox, Modal, Sev, Status } from './ui.jsx'

function NewApplication({ categories, onClose }) {
  const [insts, setInsts] = useState([])
  const [inst, setInst] = useState('')
  const [cat, setCat] = useState(categories?.[0]?.id || '')
  const [files, setFiles] = useState([])
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  useEffect(() => { api.institutions().then(l => { setInsts(l); if (l.length) setInst(l[0].id) }).catch(setErr) }, [])
  const need = categories?.find(c => c.id === cat)?.required_documents || []
  const submit = async e => {
    e.preventDefault(); setBusy(true); setErr(null)
    try {
      const a = await api.createApp(inst, cat)
      for (let i = 0; i < files.length; i++) await api.upload(a.id, files[i], null, i === files.length - 1)  // checks run once, after the last file
      location.hash = `#/applications/${a.id}`; onClose()
    } catch (x) { setErr(x); setBusy(false) }
  }
  return (
    <Modal title="New application" onClose={onClose}>
      <form onSubmit={submit}>
        <label>Institution<select value={inst} onChange={e => setInst(e.target.value)} required>{insts.map(i => <option key={i.id} value={i.id}>{i.name} ({i.region})</option>)}</select></label>
        <label>Category<select value={cat} onChange={e => setCat(e.target.value)}>{(categories || []).map(c => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label>
        {need.length > 0 && <p className="muted small">Expected documents: {need.join(', ')}. PDF, PNG, JPEG or TIFF; scans are read with OCR.</p>}
        <label>Documents<input type="file" multiple accept=".pdf,.png,.jpg,.jpeg,.tif,.tiff,application/pdf,image/*" onChange={e => setFiles([...e.target.files])} /></label>
        <ErrorBox error={err} />
        <div className="row-end"><button type="button" className="ghost" onClick={onClose}>Cancel</button><button disabled={busy || !inst || !cat}>{busy ? 'Checking...' : files.length ? 'Create and check' : 'Create draft'}</button></div>
      </form>
    </Modal>
  )
}

export default function Queue({ categories, user }) {
  const [showNew, setShowNew] = useState(location.hash === '#/queue/new')
  const [f, setF] = useState({ q: '', status: '', category: '', region: '', min_severity: '' })
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => { api.queue(f).then(setData).catch(setErr) }, [f])
  const set = k => e => setF({ ...f, [k]: e.target.value })
  const t = data?.totals
  return (
    <div>
      <h2>{user?.role === 'institution_user' ? 'My applications' : 'Scrutiny queue'}</h2>
      {user?.role !== 'admin_senior' && <p className="muted">Showing only {user?.role === 'institution_user' ? 'your institution' : `region ${user?.region}`}.</p>}
      <div className="tiles">
        {['high', 'medium', 'low'].map(s => <div key={s} className={`tile sev-${s}`}><div className="num">{t ? t[s] : '-'}</div><div>{s} findings open</div></div>)}
        <div className="tile"><div className="num">{t ? t.manual_review : '-'}</div><div>need manual review</div></div>
        <div className="tile"><div className="num">{data ? data.count : '-'}</div><div>applications</div></div>
      </div>
      <div className="filters"><button onClick={() => setShowNew(true)}>New application</button></div>
      {showNew && <NewApplication categories={categories} onClose={() => setShowNew(false)} />}
      <div className="filters">
        <input placeholder="Search application or institution" value={f.q} onChange={set('q')} aria-label="Search" />
        <select value={f.status} onChange={set('status')} aria-label="Status">
          <option value="">Any status</option>
          {['draft', 'in_scrutiny', 'awaiting_clarification', 'reviewed'].map(s => <option key={s} value={s}>{s.replace('_', ' ')}</option>)}
        </select>
        <select value={f.category} onChange={set('category')} aria-label="Category">
          <option value="">Any category</option>
          {(categories || []).map(c => <option key={c.id} value={c.id}>{c.title}</option>)}
        </select>
        <select value={f.region} onChange={set('region')} aria-label="Region">
          <option value="">Any region</option>
          {['CRO', 'ERO', 'NRO', 'SRO', 'SWRO', 'WRO', 'NWRO'].map(r => <option key={r}>{r}</option>)}
        </select>
        <select value={f.min_severity} onChange={set('min_severity')} aria-label="Minimum severity">
          <option value="">Any severity</option><option value="high">High</option><option value="medium">Medium and above</option><option value="low">Low and above</option>
        </select>
      </div>
      <ErrorBox error={err} />
      {data && !data.items.length && <Empty>No applications match these filters.</Empty>}
      {data && data.items.length > 0 && (
        <table>
          <thead><tr><th>Application</th><th>Institution</th><th>Category</th><th>Region</th><th>Submitted</th><th>Status</th><th>Open findings</th></tr></thead>
          <tbody>
            {data.items.map(i => (
              <tr key={i.id} className="clickable" onClick={() => { location.hash = `#/applications/${i.id}` }}>
                <td><a href={`#/applications/${i.id}`}>{i.id}</a></td>
                <td>{i.institution_name}</td>
                <td>{i.category.replace('eoa_', '').replace('_', ' ')}</td>
                <td>{i.region}</td>
                <td>{i.submission_date}</td>
                <td><Status s={i.status} /></td>
                <td className="counts">
                  {['high', 'medium', 'low'].map(s => i.counts[s] > 0 && <span key={s}><Sev s={s} /> {i.counts[s]} </span>)}
                  {i.counts.manual_review > 0 && <span className="badge st-manual_review">{i.counts.manual_review} manual</span>}
                  {i.counts.open_total + i.counts.manual_review === 0 && <span className="muted">none</span>}
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      )}
    </div>
  )
}
