import React, { useEffect, useRef, useState } from 'react'
import { api, session } from './api.js'
import Viewer from './Viewer.jsx'
import { Empty, ErrorBox, Mock, Modal, Pct, Sev, SOURCE_LABEL, Status, fmt } from './ui.jsx'

const ACTIONS = [
  ['accept', 'Accept finding', 'The finding is valid; it stays on record for follow-up.'],
  ['override', 'Override (dismiss)', 'The finding is not a concern after human review.'],
  ['request_clarification', 'Request clarification', 'Ask the institution to explain or resubmit.'],
]

function ActionModal({ finding, onClose, onDone }) {
  const [action, setAction] = useState('accept')
  const [reason, setReason] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const submit = async e => {
    e.preventDefault(); setBusy(true); setErr(null)
    try { await api.act(finding.id, action, reason); onDone() } catch (x) { setErr(x); setBusy(false) }
  }
  return (
    <Modal title={`Review finding ${finding.rule_id}`} onClose={onClose}>
      <form onSubmit={submit}>
        <p className="muted">{finding.message}</p>
        {ACTIONS.map(([v, l, d]) => (
          <label key={v} className="radio"><input type="radio" name="a" checked={action === v} onChange={() => setAction(v)} /> <strong>{l}</strong><br /><span className="muted">{d}</span></label>
        ))}
        <label>Reason (required, recorded in the audit trail)
          <textarea rows={3} value={reason} onChange={e => setReason(e.target.value)} minLength={5} required />
        </label>
        <ErrorBox error={err} />
        <div className="row-end"><button type="button" className="ghost" onClick={onClose}>Cancel</button><button disabled={busy || reason.trim().length < 5}>Record decision</button></div>
      </form>
    </Modal>
  )
}

function Evidence({ f, actions, onAct, canReview }) {
  const { check, doc_type, ...rest } = f.details || {}
  return (
    <div className="evidence">
      {f.evidence_text && <p><strong>Evidence:</strong> {f.evidence_text}</p>}
      <dl>
        <dt>Rule</dt><dd>{f.rule_id} ({SOURCE_LABEL[f.source] || f.source})</dd>
        {f.confidence != null && <><dt>Confidence</dt><dd><Pct v={f.confidence} /></dd></>}
        {f.page && <><dt>Location</dt><dd>page {f.page}{f.bbox ? ' (highlighted)' : ''}</dd></>}
        {Object.entries(rest).filter(([, v]) => typeof v !== 'object').map(([k, v]) => <React.Fragment key={k}><dt>{k.replace(/_/g, ' ')}</dt><dd>{String(v)}</dd></React.Fragment>)}
      </dl>
      {f.source === 'signature' && f.details?.signatures && (
        <div className="sigbox"><strong>Signature check</strong> <Mock label="MOCK trust root" />
          {f.details.signatures.map((s, i) => <dl key={i} className="small"><dt>Status</dt><dd>{s.status}</dd><dt>Signer</dt><dd>{s.signer_cn} / {s.signer_org}</dd><dt>Valid</dt><dd>{(s.not_before || '').slice(0, 10)} to {(s.not_after || '').slice(0, 10)}</dd><dt>Intact</dt><dd>{String(s.intact)} (coverage {String(s.coverage || '').replace('SignatureCoverageLevel.', '')})</dd><dt>Chain trusted</dt><dd>{String(s.trusted)}</dd></dl>)}
        </div>)}
      {f.source === 'tamper' && <p className="muted small">Image-forensic heuristic ({f.details?.indicator}, score {f.details?.score}). Scan artefacts can trigger it; compare against the original paper document.</p>}
      {actions.length > 0 && <ul className="history">{actions.map(a => <li key={a.id}><strong>{a.action.replace('_', ' ')}</strong> by {a.actor} - {a.reason} <span className="muted">({fmt(a.created_at)})</span></li>)}</ul>}
      {canReview ? <button onClick={onAct}>Review...</button> : <span className="muted">Review decisions are made by AICTE scrutiny officers.</span>}
      <p className="muted small">Indicator for human review - not proof of forgery or misconduct.</p>
    </div>
  )
}

export default function Detail({ id }) {
  const [d, setD] = useState(null)
  const [err, setErr] = useState(null)
  const [sel, setSel] = useState(null)
  const [docId, setDocId] = useState(null)
  const [page, setPage] = useState(1)
  const [modal, setModal] = useState(null)
  const [busy, setBusy] = useState(false)
  const [audit, setAudit] = useState(null)
  const [showInactive, setShowInactive] = useState(false)
  const fileRef = useRef()
  const [replaces, setReplaces] = useState('')

  const load = () => api.app(id).then(x => { setD(x); setErr(null); setDocId(c => c ?? x.documents.find(y => y.is_current)?.id ?? null) }).catch(setErr)
  useEffect(() => { setD(null); setSel(null); setDocId(null); setPage(1); load() }, [id])

  if (err && !d) return <ErrorBox error={err} />
  if (!d) return <p>Loading...</p>
  const doc = d.documents.find(x => x.id === docId)
  const shown = d.findings.filter(f => showInactive || f.active)
  const selected = d.findings.find(f => f.id === sel)
  const pick = f => {
    setSel(f.id)
    if (f.document_id) { setDocId(f.document_id) }
    if (f.page) setPage(f.page)
  }
  const upload = async e => {
    e.preventDefault()
    const file = fileRef.current.files[0]
    if (!file) return
    setBusy(true); setErr(null)
    try { await api.upload(id, file, replaces || null); fileRef.current.value = ''; setReplaces(''); await load() } catch (x) { setErr(x) }
    setBusy(false)
  }
  const rerun = async () => { setBusy(true); try { await api.run(id); await load() } catch (x) { setErr(x) } setBusy(false) }
  const loadAudit = () => api.appAudit(id).then(setAudit).catch(setErr)
  const c = d.application.counts
  const canReview = session.user?.role !== 'institution_user'

  return (
    <div>
      <p><a href="#/queue">&larr; Queue</a></p>
      <div className="head">
        <div>
          <h2>{d.application.id} <Status s={d.application.status} /></h2>
          <div className="muted">{d.institution.name} - {d.institution.city}, {d.institution.state} ({d.institution.region}) - {d.checklist.title} - submitted {d.application.submission_date}</div>
        </div>
        <div className="head-actions">
          {canReview && <button className="ghost" onClick={rerun} disabled={busy}>Re-run checks</button>}
          {canReview && <button className="ghost" onClick={() => api.openReport(id).catch(setErr)}>Report (JSON)</button>}
        </div>
      </div>
      <ErrorBox error={err} />
      <div className="tiles small">
        {['high', 'medium', 'low'].map(s => <div key={s} className={`tile sev-${s}`}><div className="num">{c[s]}</div><div>{s}</div></div>)}
        <div className="tile"><div className="num">{c.manual_review}</div><div>manual review</div></div>
        {d.missing_documents.length > 0 && <div className="tile sev-high"><div className="num">{d.missing_documents.length}</div><div>missing: {d.missing_documents.join(', ')}</div></div>}
      </div>

      <div className="cols">
        <div className="col-left">
          <h3>Findings <label className="inline small"><input type="checkbox" checked={showInactive} onChange={e => setShowInactive(e.target.checked)} /> include superseded</label></h3>
          {!shown.length && <Empty>No findings for this application.</Empty>}
          <ul className="findings">
            {shown.map(f => (
              <li key={f.id} className={`${sel === f.id ? 'sel' : ''} ${f.active ? '' : 'inactive'}`}>
                <button className="finding" onClick={() => pick(f)}>
                  <Sev s={f.severity} /> <Status s={f.status} /> {f.source === 'verification' && <Mock />} {!f.active && <span className="badge">superseded</span>}
                  <div>{f.message}</div>
                </button>
                {sel === f.id && <Evidence f={f} actions={d.reviewer_actions.filter(a => a.finding_id === f.id)} onAct={() => setModal(f)} canReview={canReview} />}
              </li>
            ))}
          </ul>

          <h3>Verifications <Mock label="MOCK issuer" /></h3>
          {!d.verifications.length ? <Empty>No references to verify.</Empty> : (
          <ul className="timeline">
            {d.verifications.map(v => (
              <li key={v.id} className={`tl-${v.status}`}>
                <div><strong>{v.status.replace('_', ' ')}</strong> <span className="muted">{v.doc_type} - {v.ref_id || 'no reference'}</span></div>
                <div className="muted small">{fmt(v.timestamp)} - run {v.run_id}{v.reason ? ` - ${v.reason}` : ''}</div>
                {(v.attempts || []).length > 0 && <div className="small">{v.attempts.map(a => <span key={a.attempt} className={`att att-${a.outcome}`} title={a.error || ''}>try {a.attempt}: {a.outcome.replace('_', ' ')}{a.backoff_s ? ` (wait ${a.backoff_s}s)` : ''}</span>)}</div>}
              </li>
            ))}
          </ul>
          )}

          <h3>Institution record <Mock /></h3>
          {d.mock_record ? <dl className="small"><dt>Name</dt><dd>{d.mock_record.name}</dd><dt>Approved intake</dt><dd>{d.mock_record.approved_intake}</dd><dt>Last EoA valid until</dt><dd>{d.mock_record.last_eoa_valid_until}</dd></dl> : <Empty>No record.</Empty>}

          {canReview && <h3>Audit trail</h3>}
          {canReview && (!audit ? <button className="ghost" onClick={loadAudit}>Show audit entries</button> : (
            <ul className="history">{audit.slice().reverse().map(a => <li key={a.id}><strong>{a.action}</strong> - {a.actor} <span className="muted">{fmt(a.ts)}</span></li>)}</ul>
          ))}
        </div>

        <div className="col-right">
          <h3>Documents</h3>
          <table className="compact"><thead><tr><th>Type</th><th>File</th><th>Ver.</th><th>Checks</th></tr></thead><tbody>
            {d.documents.map(x => (
              <tr key={x.id} className={`${x.id === docId ? 'sel' : ''} ${x.is_current ? '' : 'inactive'} clickable`} onClick={() => { setDocId(x.id); setPage(1) }}>
                <td>{x.doc_type}</td><td>{x.filename}</td><td>v{x.version}{x.is_current ? '' : ' (old)'}</td>
                <td className="small">
                  {x.classification.confidence != null && <span>cls <Pct v={x.classification.confidence} /> </span>}
                  {x.source && x.source !== 'text_layer' && <span className="badge">scan / OCR</span>}
                  {x.signature && x.signature.status !== 'no_signature' && <span className={`badge ${x.signature.status === 'valid' ? 'ok-b' : 'sev-high'}`} title="Signature checked against the MOCK trust root">sig: {x.signature.status.replace(/_/g, ' ')}</span>}
                  {(x.tamper || []).length > 0 && <span className="badge sev-medium">{x.tamper.length} tamper indicator{x.tamper.length > 1 ? 's' : ''}</span>}
                </td>
              </tr>
            ))}
          </tbody></table>
          <form className="upload" onSubmit={upload}>
            <input type="file" accept="application/pdf,image/png,image/jpeg,image/tiff" ref={fileRef} aria-label="PDF or scanned image to upload" />
            <select value={replaces} onChange={e => setReplaces(e.target.value)} aria-label="Replaces">
              <option value="">New document</option>
              {d.documents.filter(x => x.is_current).map(x => <option key={x.id} value={x.id}>Replace {x.doc_type} ({x.filename})</option>)}
            </select>
            <button disabled={busy}>Upload and re-check</button>
          </form>
          {doc ? <Viewer doc={doc} page={page} setPage={setPage} highlight={selected && selected.document_id === doc.id ? selected : null} /> : <Empty>Select a document to view it.</Empty>}
          {doc && (
            <details><summary>Extracted fields ({doc.fields.length})</summary>
              <table className="compact"><thead><tr><th>Field</th><th>Value</th><th>Conf.</th></tr></thead><tbody>
                {doc.fields.map(f => <tr key={f.name} onClick={() => setPage(f.page || 1)} className="clickable"><td>{f.name}</td><td>{String(f.value)}</td><td><Pct v={f.confidence} /></td></tr>)}
              </tbody></table>
            </details>
          )}
        </div>
      </div>
      {modal && <ActionModal finding={modal} onClose={() => setModal(null)} onDone={() => { setModal(null); load(); setAudit(null) }} />}
    </div>
  )
}
