import React, { useEffect, useState } from 'react'
import { api } from './api.js'
import { Empty, ErrorBox, Mock, fmt } from './ui.jsx'

export default function Notifications({ onRead }) {
  const [data, setData] = useState(null)
  const [err, setErr] = useState(null)
  const load = () => api.notifications().then(setData).catch(setErr)
  useEffect(() => { load() }, [])
  const readAll = async () => { await api.markRead().catch(setErr); load(); onRead && onRead() }
  return (
    <div>
      <h2>Notifications</h2>
      <p className="muted">In-app notifications, plus the e-mail channel which is <Mock label="MOCK" />: e-mails are only recorded here, never sent.</p>
      <div className="filters"><button className="ghost" onClick={readAll} disabled={!data?.unread}>Mark all read ({data?.unread ?? 0})</button></div>
      <ErrorBox error={err} />
      {data && !data.items.length && <Empty>Nothing yet.</Empty>}
      <ul className="notes">
        {(data?.items || []).map(n => (
          <li key={n.id} className={n.read ? '' : 'unread'}>
            <div><strong>{n.subject}</strong> {n.mock && <Mock label="MOCK e-mail" />}</div>
            <div className="muted small">{fmt(n.created_at)}{n.application_id && <> &middot; <a href={`#/applications/${n.application_id}`}>{n.application_id}</a></>}</div>
            <div className="small">{n.body.split('\n')[0]}</div>
          </li>
        ))}
      </ul>
    </div>
  )
}
