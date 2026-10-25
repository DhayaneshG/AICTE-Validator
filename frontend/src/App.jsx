import React, { useEffect, useState } from 'react'
import { api, session } from './api.js'
import Alerts from './Alerts.jsx'
import APH from './APH.jsx'
import AuditView from './AuditView.jsx'
import Detail from './Detail.jsx'
import Home from './Home.jsx'
import Login from './Login.jsx'
import Notifications from './Notifications.jsx'
import Queue from './Queue.jsx'
import Reports from './Reports.jsx'
import { Banner, Mock } from './ui.jsx'

function useRoute() {
  const [h, setH] = useState(location.hash || '#/')
  useEffect(() => { const f = () => setH(location.hash || '#/'); addEventListener('hashchange', f); return () => removeEventListener('hashchange', f) }, [])
  return h.replace(/^#/, '')
}

const DAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday']
const p2 = n => String(n).padStart(2, '0')
function Clock() {
  const [d, setD] = useState(new Date())
  useEffect(() => { const t = setInterval(() => setD(new Date()), 30000); return () => clearInterval(t) }, [])
  const h = d.getHours() % 12 || 12
  return <span>{DAYS[d.getDay()]} , {p2(d.getMonth() + 1)}-{p2(d.getDate())}-{d.getFullYear()} ,{p2(h)}:{p2(d.getMinutes())} {d.getHours() < 12 ? 'AM' : 'PM'}</span>
}

function setSize(px) { try { document.documentElement.style.fontSize = px + 'px'; sessionStorage.setItem('aicte_fs', String(px)) } catch { /* ignore */ } }

function Emblem() { // neutral placeholder mark - deliberately NOT the official AICTE emblem
  return (
    <svg width="64" height="64" viewBox="0 0 64 64" role="img" aria-label="EoA Validation Console">
      <circle cx="32" cy="32" r="30" fill="#fdb515" stroke="#0a2a6b" strokeWidth="3" />
      <circle cx="32" cy="32" r="20" fill="#fff" stroke="#0a2a6b" strokeWidth="2" />
      <path d="M22 33l7 7 13-15" fill="none" stroke="#0a2a6b" strokeWidth="5" strokeLinecap="round" strokeLinejoin="round" />
    </svg>
  )
}

export default function App() {
  const route = useRoute()
  const [meta, setMeta] = useState(null)
  useEffect(() => { api.meta().then(setMeta).catch(() => setMeta(null)) }, [])
  useEffect(() => { try { const v = Number(sessionStorage.getItem('aicte_fs')); if (v) setSize(v) } catch { /* ignore */ } }, [])
  const [user, setUser] = useState(session.user || null)
  useEffect(() => { session.onExpire = () => setUser(null) }, [])
  const [unread, setUnread] = useState(0)
  const refreshUnread = () => { if (session.token) api.notifications({ unread_only: true, limit: 1 }).then(j => setUnread(j.unread)).catch(() => {}) }
  useEffect(() => { refreshUnread(); const t = setInterval(refreshUnread, 30000); return () => clearInterval(t) }, [user, route])
  const logout = () => { api.logout(); setUser(null); location.hash = '#/' }
  const staff = user && user.role !== 'institution_user'
  const m = route.match(/^\/applications\/([\w-]+)/)
  const link = (to, label, extra) => <a href={`#${to}`} className={route === to || (to === '/queue' && route.startsWith('/queue')) ? 'active' : ''}>{label}{extra}</a>
  let page
  if (!user) page = <Login onLogin={setUser} />
  else if (m) page = <Detail id={m[1]} />
  else if (route.startsWith('/queue')) page = <Queue categories={meta?.categories} user={user} />
  else if (route === '/alerts' && staff) page = <Alerts />
  else if (route === '/reports' && staff) page = <Reports />
  else if (route === '/aph') page = <APH />
  else if (route === '/notifications') page = <Notifications onRead={refreshUnread} />
  else if (route === '/audit' && user.role === 'admin_senior') page = <AuditView />
  else page = <Home user={user} meta={meta} />
  return (
    <>
      <div className="utility"><div className="in">
        <Clock />
        <a href="#main">Skip to content</a>
        <span className="spacer" />
        <span>Text Size</span>
        <button className="tsz" onClick={() => setSize(13)} aria-label="Decrease text size">A-</button>
        <button className="tsz" onClick={() => setSize(14)} aria-label="Default text size">A</button>
        <button className="tsz" onClick={() => setSize(16)} aria-label="Increase text size">A+</button>
        {meta && <details className="mocks"><summary>Simulated components <Mock /></summary><ul>{meta.mock_components.map(c => <li key={c.name}><strong>{c.name}</strong><br /><span className="muted">{c.note}</span></li>)}</ul></details>}
        {user
          ? <span className="who" title={user.full_name || ''}>{user.username} <span className="badge">{user.role.replace('_', ' ')}</span>{user.role === 'institution_user' ? '' : ` ${user.region || 'all regions'}`} <button className="pill" onClick={logout}>Sign out</button></span>
          : <a className="pill" href="#/">Web Portal Login</a>}
      </div></div>
      <div className="brandbar"><div className="in">
        <Emblem />
        <div><div className="t1">EoA Validation Console</div><div className="t2">Extension of Approval scrutiny support &middot; prototype with synthetic data &middot; not an official AICTE website</div></div>
      </div></div>
      <nav className="mainnav" aria-label="Main"><div className="in">
        {link('/', 'Home')}
        {user && link('/queue', user.role === 'institution_user' ? 'My Applications' : 'Scrutiny Queue')}
        {staff && link('/alerts', 'Alerts')}
        {staff && link('/reports', 'Reports')}
        {user && link('/aph', 'APH Checklist')}
        {user && link('/notifications', 'Notifications', unread > 0 ? <span className="count">{unread}</span> : null)}
        {user && user.role === 'admin_senior' && link('/audit', 'Audit')}
        {user && <a href="#/queue">Search</a>}
      </div></nav>
      <Banner text={meta?.advisory} />
      <main id="main">{page}</main>
      <div className="footer">Prototype built for demonstration with synthetic data. Not an official AICTE website. Findings are advisory indicators for human review.</div>
    </>
  )
}
