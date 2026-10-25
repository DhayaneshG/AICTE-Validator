import React from 'react'

export const SEV_LABEL = { high: 'High', medium: 'Medium', low: 'Low' }
export const STATUS_LABEL = {
  open: 'Open', manual_review: 'Manual review', accepted: 'Accepted', overridden: 'Overridden', clarification_requested: 'Clarification requested',
  draft: 'Draft', in_scrutiny: 'In scrutiny', awaiting_clarification: 'Awaiting clarification', reviewed: 'Reviewed',
}
export const SOURCE_LABEL = { rule: 'Rule', anomaly: 'Anomaly', verification: 'Verification', authenticity: 'Authenticity', signature: 'Signature', tamper: 'Tamper indicator' }

export const Sev = ({ s }) => <span className={`badge sev-${s}`}>{SEV_LABEL[s] || s}</span>
export const Status = ({ s }) => <span className={`badge st-${s}`}>{STATUS_LABEL[s] || s}</span>
export const Mock = ({ label = 'MOCK' }) => <span className="badge mock" title="Simulated data source - not a real AICTE or issuer system">{label}</span>
export const Pct = ({ v }) => <span className={v < 0.6 ? 'lowconf' : ''}>{Math.round(v * 100)}%</span>
export const fmt = d => (d ? d.replace('T', ' ').slice(0, 19) : '')

export function Banner({ text }) {
  return (
    <div className="advisory" role="note">
      <strong>Advisory only.</strong> {(text || 'Indicator for human review - not proof of forgery or misconduct.').replace(/^Advisory( indicator)?:? ?/i, m => (m.toLowerCase().includes('indicator') ? 'Indicator ' : ''))}
      <span className="synthetic">All data in this build is synthetic.</span>
    </div>
  )
}

export function Modal({ title, onClose, children }) {
  return (
    <div className="modal-bg" onClick={onClose}>
      <div className="modal" role="dialog" aria-modal="true" aria-label={title} onClick={e => e.stopPropagation()}>
        <div className="modal-head"><h3>{title}</h3><button className="ghost" onClick={onClose} aria-label="Close">x</button></div>
        {children}
      </div>
    </div>
  )
}

export function Empty({ children }) { return <div className="empty">{children}</div> }
export function ErrorBox({ error }) { return error ? <div className="error" role="alert">{error.message || String(error)}</div> : null }
