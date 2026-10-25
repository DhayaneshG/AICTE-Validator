// Thin API client. JWT bearer token kept in sessionStorage (cleared on tab close); 401 -> back to the login page.
const BASE = '/api'
const KEY = 'aicte_session'
function load() { try { return JSON.parse(sessionStorage.getItem(KEY)) || {} } catch { return {} } }
function save(v) { try { v ? sessionStorage.setItem(KEY, JSON.stringify(v)) : sessionStorage.removeItem(KEY) } catch { /* private mode: memory only */ } }
export const session = { ...load(), onExpire: () => {} }
export function setSession(token, user) { session.token = token; session.user = user; save(token ? { token, user } : null) }

async function req(path, opts = {}) {
  const headers = { ...(session.token ? { Authorization: `Bearer ${session.token}` } : {}), ...(opts.headers || {}) }
  const r = await fetch(BASE + path, { ...opts, headers })
  if (r.status === 401 && session.token) { setSession(null, null); session.onExpire() }
  if (!r.ok) {
    let msg = r.statusText
    try { const j = await r.json(); msg = typeof j.detail === 'string' ? j.detail : JSON.stringify(j.detail) } catch { /* ignore */ }
    const e = new Error(msg); e.status = r.status; throw e
  }
  return opts.raw ? r : r.json()
}
const jsonBody = (method, body) => ({ method, headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) })

const qs = params => new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))
export const blobUrl = async path => URL.createObjectURL(await (await req(path, { raw: true })).blob())

export const api = {
  login: async (username, password) => {
    const r = await fetch(BASE + '/auth/login', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ username, password }) })
    const j = await r.json().catch(() => ({}))
    if (!r.ok) { const e = new Error(typeof j.detail === 'string' ? j.detail : 'login failed'); e.status = r.status; throw e }
    setSession(j.access_token, j.user); return j.user
  },
  logout: () => setSession(null, null),
  meta: () => req('/meta'),
  aphCrosscheck: () => req('/aph-crosscheck'),
  queue: (params = {}) => req('/applications?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))),
  app: id => req(`/applications/${id}`),
  run: id => req(`/applications/${id}/run`, { method: 'POST' }),
  institutions: () => req('/institutions'),
  createApp: (institution_id, category, submission_date) => req('/applications', jsonBody('POST', { institution_id, category, ...(submission_date ? { submission_date } : {}) })),
  upload: (id, file, replaces, run = true) => {
    const f = new FormData(); f.append('file', file); if (replaces) f.append('replaces_document_id', replaces); f.append('run', run ? 'true' : 'false')
    return req(`/applications/${id}/documents`, { method: 'POST', body: f })
  },
  act: (fid, action, reason) => req(`/findings/${fid}/actions`, jsonBody('POST', { action, reason })),
  audit: (params = {}) => req('/audit?' + new URLSearchParams(Object.entries(params).filter(([, v]) => v !== '' && v != null))),
  auditVerify: () => req('/audit/verify'),
  appAudit: id => req(`/applications/${id}/audit`),
  report: id => req(`/applications/${id}/report`),
  pagePath: (docId, page, dpi = 110) => `/documents/${docId}/page/${page}.png?dpi=${dpi}`,  // images need the Authorization header -> fetched as blobs
  openReport: async id => { const j = await req(`/applications/${id}/report`); window.open(URL.createObjectURL(new Blob([JSON.stringify(j, null, 2)], { type: 'application/json' })), '_blank') },
  alerts: (params = {}) => req('/alerts?' + qs(params)),
  ackAlert: id => req(`/alerts/${id}/acknowledge`, { method: 'POST' }),
  evaluateAlerts: () => req('/alerts/evaluate', { method: 'POST' }),
  notifications: (params = {}) => req('/notifications?' + qs(params)),
  markRead: () => req('/notifications/read', { method: 'POST' }),
  summary: () => req('/reports/summary'),
  download: async (path, filename) => {
    const r = await req(path, { raw: true }); const u = URL.createObjectURL(await r.blob())
    const a = document.createElement('a'); a.href = u; a.download = filename; document.body.appendChild(a); a.click(); a.remove(); setTimeout(() => URL.revokeObjectURL(u), 2000)
  },
  retryVerifications: () => req('/verifications/retry', { method: 'POST' }),
}
