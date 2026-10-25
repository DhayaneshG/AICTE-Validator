import React, { useEffect, useState } from 'react'
import { api, blobUrl } from './api.js'

function useBlob(path) {
  const [u, setU] = useState(null)
  const [err, setErr] = useState(null)
  useEffect(() => {
    let url = null, dead = false
    setU(null); setErr(null)
    blobUrl(path).then(x => { url = x; dead ? URL.revokeObjectURL(x) : setU(x) }).catch(e => !dead && setErr(e))
    return () => { dead = true; if (url) URL.revokeObjectURL(url) }
  }, [path])
  return [u, err]
}

// Page image (rendered server-side) with bbox overlays. Boxes are PDF points, origin top-left -> percentages.
export default function Viewer({ doc, page, setPage, highlight }) {
  const [showFields, setShowFields] = useState(true)
  const [showTamper, setShowTamper] = useState(true)
  const [imgUrl, imgErr] = useBlob(api.pagePath(doc.id, page))
  const info = doc.pages_info.find(p => p.page === page) || doc.pages_info[0]
  if (!info) return <div className="empty">No pages</div>
  const pct = b => ({ left: `${(b[0] / info.width) * 100}%`, top: `${(b[1] / info.height) * 100}%`, width: `${((b[2] - b[0]) / info.width) * 100}%`, height: `${((b[3] - b[1]) / info.height) * 100}%` })
  const fields = (doc.fields || []).filter(f => f.page === page && f.bbox)
  const tampers = (doc.tamper || []).filter(t => t.page === page && t.bbox)
  const src = highlight && highlight.details && highlight.details.source_bbox
  return (
    <div className="viewer">
      <div className="viewer-bar">
        <button className="ghost" disabled={page <= 1} onClick={() => setPage(page - 1)}>Prev</button>
        <span>Page {page} / {doc.page_count}</span>
        <button className="ghost" disabled={page >= doc.page_count} onClick={() => setPage(page + 1)}>Next</button>
        <label className="inline"><input type="checkbox" checked={showFields} onChange={e => setShowFields(e.target.checked)} /> extracted fields</label>
        {(doc.tamper || []).length > 0 && <label className="inline"><input type="checkbox" checked={showTamper} onChange={e => setShowTamper(e.target.checked)} /> tamper indicators ({doc.tamper.length})</label>}
      </div>
      <div className="page-wrap">
        {imgUrl ? <img src={imgUrl} alt={`${doc.doc_type} page ${page}`} /> : <div className="empty">{imgErr ? `Page unavailable: ${imgErr.message}` : 'Loading page...'}</div>}
        {showFields && fields.map(f => <div key={f.name} className="box field" style={pct(f.bbox)} title={`${f.name}: ${f.value} (${Math.round(f.confidence * 100)}%)`} />)}
        {showTamper && tampers.map((t, i) => <div key={i} className="box tamper" style={pct(t.bbox)} title={`${t.type} indicator (score ${t.score}) - advisory`} />)}
        {highlight && highlight.page === page && highlight.bbox && <div className="box hl" style={pct(highlight.bbox)} />}
        {highlight && highlight.page === page && src && <div className="box hl2" style={pct(src)} title="Matching source block (copy-move)" />}
      </div>
    </div>
  )
}
