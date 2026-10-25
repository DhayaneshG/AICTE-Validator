import React, { useState } from 'react'
import { api } from './api.js'
import { ErrorBox } from './ui.jsx'

const DEMO = [
  ['inst.a', 'Institution user (sees only its own institution)'],
  ['scrutiny.swro', 'Scrutiny officer, South-Western region (same region as inst.a)'],
  ['regional.swro', 'Regional officer, South-Western region'],
  ['admin', 'Senior administrator (all regions, audit, purge)'],
]

export default function Login({ onLogin }) {
  const [u, setU] = useState('')
  const [p, setP] = useState('')
  const [err, setErr] = useState(null)
  const [busy, setBusy] = useState(false)
  const submit = async e => {
    e.preventDefault(); setBusy(true); setErr(null)
    try { onLogin(await api.login(u.trim(), p)) } catch (x) { setErr(x); setBusy(false) }
  }
  return (
    <div className="login">
      <h2>Sign in</h2>
      <form onSubmit={submit}>
        <label>Username<input value={u} onChange={e => setU(e.target.value)} autoComplete="username" required autoFocus /></label>
        <label>Password<input type="password" value={p} onChange={e => setP(e.target.value)} autoComplete="current-password" required /></label>
        <ErrorBox error={err} />
        <button disabled={busy}>Sign in</button>
      </form>
      <details className="demo-logins">
        <summary>Demo accounts (synthetic data only)</summary>
        <p className="muted">Password for every demo account: <code>Demo@1234</code></p>
        <ul>{DEMO.map(([n, d]) => <li key={n}><button type="button" className="link" onClick={() => { setU(n); setP('Demo@1234') }}><code>{n}</code></button> {d}</li>)}</ul>
      </details>
    </div>
  )
}
