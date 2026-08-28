import { useState, useEffect } from 'react'
import { C } from '../utils/colors'
import { severityColor, normalizeSeverity } from '../styles/theme'
import { Card } from '../components/Card'
import { Chip } from '../components/Common'

function getSeverity(inc) {
  const raw = inc.structured?.severity || inc.anomalies?.[0]?.niveau || inc.statut || inc.niveau || ''
  return normalizeSeverity(raw) || 'MONITORING'
}

function grouperParSeverite(anomalies = []) {
  const crit  = anomalies.filter(a => String(a.niveau||'').toUpperCase().includes('CRIT'))
  const haute = anomalies.filter(a => String(a.niveau||'').toUpperCase().includes('IMP') && !crit.includes(a))
  const autre = anomalies.filter(a => !crit.includes(a) && !haute.includes(a))
  return { crit, haute, autre }
}
const SEV_ICON = { CRITICAL:'🔴', HIGH:'🟠', MONITORING:'🟡' }

function mdInline(t = '') {
  return t
    .replace(/\*\*([^*]+)\*\*/g, '<strong style="color:#f1f5f9;font-weight:600">$1</strong>')
    .replace(/`([^`]+)`/g, '<code style="background:#0f172a;padding:1px 5px;border-radius:3px;font-family:JetBrains Mono,monospace;font-size:11px;color:#a78bfa">$1</code>')
}

function TextBlock({ text, color = C.sub }) {
  const lines = text.split('\n')
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
      {lines.map((line, i) => {
        const s = line.trim()
        if (!s) return <div key={i} style={{ height:6 }}/>
        if (s.startsWith('- ') || s.startsWith('▸')) {
          const content = s.replace(/^[-▸]\s*/, '')
          return (
            <div key={i} style={{ display:'flex', gap:8, alignItems:'flex-start', paddingLeft:4 }}>
              <span style={{ color:C.blue, flexShrink:0, marginTop:2, fontSize:10 }}>▸</span>
              <span style={{ fontSize:13, color, lineHeight:1.6 }}
                dangerouslySetInnerHTML={{ __html: mdInline(content) }}/>
            </div>
          )
        }
        return <div key={i} style={{ fontSize:13, color, lineHeight:1.6 }}
          dangerouslySetInnerHTML={{ __html: mdInline(s) }}/>
      })}
    </div>
  )
}

function CodeBlock({ code, lang = 'bash' }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(code)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div style={{ borderRadius:8, overflow:'hidden', border:`1px solid #1e3a5f`, margin:'8px 0' }}>
      <div style={{ background:'#040d1a', padding:'6px 14px', display:'flex', justifyContent:'space-between', alignItems:'center', borderBottom:'1px solid #1e3a5f' }}>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background:'#ef4444' }}/>
          <div style={{ width:8, height:8, borderRadius:'50%', background:'#eab308' }}/>
          <div style={{ width:8, height:8, borderRadius:'50%', background:'#22c55e' }}/>
          <span style={{ fontSize:11, color:'#3d5a80', fontFamily:'JetBrains Mono,monospace', marginLeft:6 }}>{lang}</span>
        </div>
        <button onClick={copy} style={{ background:'transparent', border:'1px solid #1e3a5f', color: copied?'#22c55e':'#3d5a80', borderRadius:5, padding:'2px 10px', fontSize:10, cursor:'pointer', fontFamily:'JetBrains Mono,monospace', transition:'all 0.2s' }}>
          {copied ? '✓ Copied' : 'Copy'}
        </button>
      </div>
      <pre style={{ background:'#020810', margin:0, padding:'14px 18px', overflowX:'auto', fontSize:12.5, lineHeight:1.8, fontFamily:'JetBrains Mono,monospace', color:'#7dd3fc', whiteSpace:'pre' }}>
        {code}
      </pre>
    </div>
  )
}

function StructuredIncident({ structured }) {
  const parseFailed      = !!structured?._parse_failed
  const depuisHistorique = structured?._source === 'history'

  if (parseFailed && !depuisHistorique) {
    return (
      <div style={{ fontSize:13, color:C.muted, lineHeight:1.6 }}>
        <div style={{ display:'flex', gap:8, alignItems:'flex-start', padding:'8px 10px', background:'#eab30810', border:`1px solid ${C.yellow}30`, borderRadius:7 }}>
          <span style={{ color:C.yellow, flexShrink:0 }}>⚠</span>
          <span style={{ fontSize:12, color:C.yellow }}>AI response could not be fully structured this time — showing available text.</span>
        </div>
        {structured?._raw && <div style={{ marginTop:10 }}>{structured._raw}</div>}
      </div>
    )
  }

  if (!structured) {
    return <div style={{ fontSize:13, color:C.muted, lineHeight:1.6 }}>No analysis available for this incident.</div>
  }

  const texte = structured._raw || structured.summary || 'No summary available — open the full report below.'
  return (
    <div style={{ display:'flex', gap:8, alignItems:'flex-start', padding:'8px 0', fontSize:13, color:C.sub, lineHeight:1.6 }}>
      <span style={{ color:C.blue, flexShrink:0, marginTop:1 }}>ℹ</span>
      <span>{texte}</span>
    </div>
  )
}

function ReportViewer({ content, reportName }) {
  const scoreMatch = content.match(/\*\*AI Score:\*\*\s*([0-9.]+)/)
  const genMatch   = content.match(/\*\*Generated:\*\*\s*(.+?)(?:\s*>|\n)/)
  const titleMatch = content.match(/^# (.+)$/m)
  const refMatch   = content.match(/\*\*Reference:\*\*\s*\[(.+?)\]/)

  const reportTitle = titleMatch?.[1] || 'Incident Report'
  const severity    = content.includes('🔴') ? 'CRITICAL' : content.includes('🟠') ? 'HIGH' : 'MONITORING'
  const sevColor    = severityColor(severity)
  const aiScore     = scoreMatch?.[1] || '—'
  const generated   = genMatch?.[1]?.trim() || '—'
  const refUrl      = refMatch?.[1] || null

  const rawSections = content.split(/^## /m).slice(1)
  const sections = rawSections.map(s => {
    const nl      = s.indexOf('\n')
    const title   = nl > -1 ? s.slice(0, nl).trim() : s.trim()
    const rawBody = nl > -1 ? s.slice(nl+1).trim() : ''
    const body = rawBody
      .replace(/^---\s*$/gm, '')
      .replace(/^Summary:\s*.+$/gim, '')
      .replace(/^\*\*Summary:\*\*\s*.+$/gim, '')
      .replace(/^Severity:\s*\w+\s*$/gim, '')
      .replace(/\n{3,}/g, '\n\n')
      .trim()
    return { title, body }
  })

  const sectionConfig = {
    'Executive Summary':           { icon:'◈', color: C.blue,   bg:'#0d1f3c' },
    'Detected Anomalies':          { icon:'⚠', color: C.red,    bg:'#1a0c0c' },
    'Root Cause Analysis':         { icon:'⬡', color: C.orange, bg:'#1a1208' },
    'Recommended Actions':         { icon:'✓', color: C.green,  bg:'#0a1a0e' },
    'Cluster State at Alert Time': { icon:'◉', color: C.cyan,   bg:'#081a1e' },
    'Thresholds Reference (Minimum Professional Floor)': { icon:'≡', color: C.sub, bg:'#0b0d11' },
  }

  function MdTable({ text }) {
    const lines = text.trim().split('\n').filter(l => l.trim().startsWith('|'))
    if (lines.length < 2) return <TextBlock text={text}/>
    const headers = lines[0].split('|').map(h => h.trim()).filter(Boolean)
    const rows    = lines.slice(2).map(l => l.split('|').map(c => c.trim()).filter(Boolean))
    return (
      <div style={{ overflowX:'auto', borderRadius:8, border:`1px solid ${C.border}` }}>
        <table style={{ width:'100%', borderCollapse:'collapse', fontSize:12 }}>
          <thead>
            <tr style={{ background:'#040d1a' }}>
              {headers.map((h,i) => (
                <th key={i} style={{ padding:'8px 14px', textAlign:'left', color:'#64748b', fontSize:11, fontWeight:700, letterSpacing:'0.08em', fontFamily:'JetBrains Mono,monospace', borderBottom:`1px solid ${C.border}` }}>{h}</th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((row, ri) => (
              <tr key={ri} style={{ background: ri%2===0 ? 'transparent' : '#04080f', borderBottom:`1px solid ${C.border}22` }}>
                {row.map((cell, ci) => {
                  let color = C.sub
                  if (cell.toUpperCase() === 'ONLINE')        color = C.green
                  if (cell.toUpperCase() === 'OFFLINE')       color = C.red
                  if (cell.toUpperCase() === 'RUNNING')       color = C.green
                  if (cell.toUpperCase() === 'STOPPED')       color = C.muted
                  if (cell.toLowerCase().includes('unreachable')) color = C.muted
                  if (cell === '—')                           color = C.border
                  if (cell.includes('⚠'))                    color = C.red
                  if (cell.includes('↑'))                    color = C.orange
                  return (
                    <td key={ci} style={{ padding:'9px 14px', color, fontFamily: ci>0 ? 'JetBrains Mono,monospace' : 'inherit', fontWeight: ci===0 ? 600 : 400, fontSize: ci===0 ? 13 : 12, fontStyle: cell.toLowerCase().includes('unreachable') ? 'italic' : 'normal' }}>
                      {cell}
                    </td>
                  )
                })}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    )
  }

  function MixedContent({ text }) {
    const parts = []
    const re    = /```(\w*)\n?([\s\S]*?)```/g
    let last = 0, m
    while ((m = re.exec(text)) !== null) {
      if (m.index > last) parts.push({ type:'text', content: text.slice(last, m.index) })
      parts.push({ type:'code', lang: m[1]||'bash', content: m[2].trim() })
      last = m.index + m[0].length
    }
    if (last < text.length) parts.push({ type:'text', content: text.slice(last) })
    return (
      <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
        {parts.map((p,i) =>
          p.type === 'code'
            ? <CodeBlock key={i} code={p.content} lang={p.lang}/>
            : <TextBlock key={i} text={p.content.trim()}/>
        )}
      </div>
    )
  }

  return (
    <div style={{ fontFamily:"'Inter','Segoe UI',sans-serif" }}>
      <div style={{ background:`linear-gradient(135deg, ${sevColor}22 0%, #030810 100%)`, borderBottom:`1px solid ${sevColor}30`, padding:'20px 24px' }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:14 }}>
          <div>
            <div style={{ fontSize:10, color:sevColor, fontWeight:700, letterSpacing:'0.14em', fontFamily:'JetBrains Mono,monospace', marginBottom:6 }}>
              OPSPILOT — INCIDENT REPORT
            </div>
            <div style={{ fontSize:18, fontWeight:800, color:C.text, lineHeight:1.3 }}>
              {reportTitle.replace(/^Incident Report — /, '')}
            </div>
          </div>
          <div style={{ display:'flex', flexDirection:'column', alignItems:'flex-end', gap:6 }}>
            <span style={{ padding:'4px 14px', borderRadius:20, fontSize:11, fontWeight:800, letterSpacing:'0.08em', background:sevColor+'20', border:`1px solid ${sevColor}50`, color:sevColor }}>
              {SEV_ICON[severity]} {severity}
            </span>
            <span style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace' }}>AI Score: {aiScore}</span>
          </div>
        </div>
        <div style={{ display:'flex', gap:20, fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', flexWrap:'wrap' }}>
          <span>📅 {generated}</span>
          <span style={{ color:C.border }}>|</span>
          <span>📄 {reportName}</span>
          <span style={{ color:C.border }}>|</span>
          <span>🤖 OpsPilot v6 · Groq</span>
          {refUrl && (<>
            <span style={{ color:C.border }}>|</span>
            <a href={refUrl} target="_blank" rel="noopener noreferrer" style={{ color:C.blue, textDecoration:'none' }}>📖 docs</a>
          </>)}
        </div>
      </div>

      {sections.map(({ title, body }, i) => {
        const cfg    = sectionConfig[title] || { icon:'▸', color:C.sub, bg:'transparent' }
        const hasTbl = body.includes('|---')
        const isAnom = title.includes('Anomal')

        let anomContent = null
        if (isAnom) {
          const critMatch = body.match(/### 🔴 Critical\n([\s\S]*?)(?=### |$)/)?.[1]?.trim()
          const highMatch = body.match(/### 🟠 High\n([\s\S]*?)(?=### |$)/)?.[1]?.trim()
          const monMatch  = body.match(/### 🟡 Monitoring\n([\s\S]*?)(?=### |$)/)?.[1]?.trim()
          anomContent = { crit: critMatch, high: highMatch, mon: monMatch }
        }

        return (
          <div key={i} style={{ borderBottom: i < sections.length-1 ? `1px solid ${C.border}22` : 'none' }}>
            <div style={{ display:'flex', alignItems:'center', gap:10, padding:'14px 24px', background: cfg.bg+'88', borderBottom:`1px solid ${cfg.color}15` }}>
              <div style={{ width:28, height:28, borderRadius:7, background:`${cfg.color}20`, border:`1px solid ${cfg.color}40`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:13, color:cfg.color, flexShrink:0 }}>
                {cfg.icon}
              </div>
              <div style={{ fontSize:11, fontWeight:700, color:cfg.color, letterSpacing:'0.1em', textTransform:'uppercase', fontFamily:'JetBrains Mono,monospace' }}>
                {title}
              </div>
            </div>
            <div style={{ padding:'16px 24px', background: cfg.bg+'44' }}>
              {isAnom && anomContent ? (
                <div style={{ display:'flex', flexDirection:'column', gap:12 }}>
                  {anomContent.crit && anomContent.crit !== '_None_' && (
                    <div style={{ background:'#ef444410', border:'1px solid #ef444430', borderRadius:8, padding:'10px 14px' }}>
                      <div style={{ fontSize:11, fontWeight:700, color:C.red, marginBottom:8, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em' }}>● CRITICAL</div>
                      <TextBlock text={anomContent.crit} color={C.sub}/>
                    </div>
                  )}
                  {anomContent.high && anomContent.high !== '_None_' && (
                    <div style={{ background:'#f9731610', border:'1px solid #f9731630', borderRadius:8, padding:'10px 14px' }}>
                      <div style={{ fontSize:11, fontWeight:700, color:C.orange, marginBottom:8, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em' }}>● HIGH</div>
                      <TextBlock text={anomContent.high} color={C.sub}/>
                    </div>
                  )}
                  {anomContent.mon && anomContent.mon !== '_None_' && (
                    <div style={{ background:'#eab30810', border:'1px solid #eab30830', borderRadius:8, padding:'10px 14px' }}>
                      <div style={{ fontSize:11, fontWeight:700, color:C.yellow, marginBottom:8, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em' }}>● MONITORING</div>
                      <TextBlock text={anomContent.mon} color={C.sub}/>
                    </div>
                  )}
                </div>
              ) : hasTbl ? (
                <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
                  {body.split('\n\n').map((chunk, ci) => {
                    const isH3    = chunk.trim().startsWith('###')
                    const hasTbl2 = chunk.includes('|---')
                    const isNote  = chunk.trim().startsWith('>')
                    if (isH3)    return <div key={ci} style={{ fontSize:11, fontWeight:700, color:C.sub, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', marginTop:8, marginBottom:4 }}>{chunk.replace(/^#+\s*/,'')}</div>
                    if (hasTbl2) return <MdTable key={ci} text={chunk}/>
                    if (isNote)  return <div key={ci} style={{ fontSize:11, color:C.muted, fontStyle:'italic', paddingLeft:8, borderLeft:`2px solid ${C.border}` }}>{chunk.replace(/^>\s*/,'')}</div>
                    return chunk.trim() ? <TextBlock key={ci} text={chunk}/> : null
                  })}
                </div>
              ) : (
                <MixedContent text={body}/>
              )}
            </div>
          </div>
        )
      })}

      <div style={{ padding:'12px 24px', background:'#040810', borderTop:`1px solid ${C.border}`, display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div style={{ fontSize:11, color:C.muted }}>OpsPilot Infrastructure AI — Auto-generated incident report</div>
        <div style={{ fontSize:10, color:C.border, fontFamily:'JetBrains Mono,monospace' }}>Do not reply · Open dashboard for live status</div>
      </div>
    </div>
  )
}

// ← AJOUT : suppression d'un incident -- même pattern "armer puis
// confirmer" que DeleteButton dans PageRecommendations.jsx (un premier
// clic arme pendant 3s, un second clic dans ce délai confirme -- pas de
// modale). Dupliqué ici plutôt qu'importé : aucun composant partagé
// n'exportait déjà ce bouton pour être réutilisé tel quel.
// ← AJOUT : remplace window.confirm() -- affichait la boîte native du
// navigateur (grise, non stylée, mélangée FR/EN selon la langue du
// navigateur), qui jurait avec le thème sombre du reste de l'app. Overlay
// + carte simple, pas de dépendance externe.
function ConfirmModal({ message, onConfirm, onCancel }) {
  return (
    <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', display:'flex', alignItems:'center', justifyContent:'center', zIndex:1000 }}
         onClick={onCancel}>
      <div onClick={e => e.stopPropagation()}
           style={{ background:C.card, border:`1px solid ${C.borderHi}`, borderRadius:12, padding:'22px 24px', maxWidth:380, boxShadow:'0 20px 60px rgba(0,0,0,0.5)' }}>
        <div style={{ fontSize:14, color:C.text, lineHeight:1.6, marginBottom:20 }}>{message}</div>
        <div style={{ display:'flex', gap:10, justifyContent:'flex-end' }}>
          <button onClick={onCancel}
            style={{ padding:'8px 16px', borderRadius:7, border:`1px solid ${C.border}`, background:'transparent', color:C.sub, fontSize:12, fontWeight:600, cursor:'pointer', fontFamily:'JetBrains Mono,monospace' }}>
            Cancel
          </button>
          <button onClick={onConfirm}
            style={{ padding:'8px 16px', borderRadius:7, border:'none', background:C.red, color:'#fff', fontSize:12, fontWeight:700, cursor:'pointer', fontFamily:'JetBrains Mono,monospace' }}>
            Delete
          </button>
        </div>
      </div>
    </div>
  )
}

function DeleteButtonInc({ onConfirm }) {
  const [armed, setArmed] = useState(false)
  const handleClick = (e) => {
    e.stopPropagation()
    if (armed) { onConfirm(); setArmed(false); return }
    setArmed(true)
    setTimeout(() => setArmed(false), 3000)
  }
  return (
    <button onClick={handleClick}
      style={{ background: armed ? C.red+'20' : 'transparent', border: `1px solid ${armed ? C.red : C.border}`,
               borderRadius: 6, color: armed ? C.red : C.muted, fontSize: 10, padding: '3px 9px',
               cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace', transition: 'all 0.15s', flexShrink: 0 }}>
      {armed ? 'Confirm?' : '🗑 Delete'}
    </button>
  )
}

function IncidentCard({ inc, selTimestamp, setSelTimestamp, closeReport, onDelete, selectionMode, selected, onToggleSelect }) {
  const sev      = getSeverity(inc)
  const sevColor = severityColor(sev)
  const isActive = selTimestamp === inc.timestamp
  return (
    <div onClick={() => selectionMode ? onToggleSelect?.(inc) : (setSelTimestamp(isActive ? null : inc.timestamp), closeReport())}
      style={{ background: isActive ? C.surface : C.card, border:`1px solid ${isActive?sevColor+'70':C.border}`, borderLeft:`3px solid ${sevColor}`, borderRadius:8, padding:'12px 14px', cursor:'pointer', transition:'all 0.15s', display:'flex', gap:10, alignItems:'flex-start' }}
      onMouseEnter={e=>{ if(!isActive) { e.currentTarget.style.background=C.surface; e.currentTarget.style.borderColor=sevColor+'40' }}}
      onMouseLeave={e=>{ if(!isActive) { e.currentTarget.style.background=C.card;    e.currentTarget.style.borderColor=C.border }}}
    >
      {selectionMode && (
        <div style={{ paddingTop: 2, flexShrink: 0 }} onClick={(e) => { e.stopPropagation(); onToggleSelect?.(inc) }}>
          <input type="checkbox" checked={selected} readOnly
                 style={{ width: 15, height: 15, cursor: 'pointer', accentColor: C.blue }} />
        </div>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8, marginBottom:6 }}>
          <div style={{ display:'flex', gap:8, alignItems:'flex-start', flex:1 }}>
            <span style={{ fontSize:12, flexShrink:0, marginTop:2 }}>{SEV_ICON[sev]||'⚪'}</span>
            <span style={{ fontSize:14, color:C.text, fontWeight:700, lineHeight:1.4 }}>
              {inc.structured?.fix_title || inc.structured?.summary || inc.anomalies?.[0]?.message || (inc.rapport ? 'Historical incident' : 'Anomaly detected')}
            </span>
          </div>
          <div style={{ display:'flex', gap:6, alignItems:'center', flexShrink:0 }}>
            <Chip label={sev} color={sevColor}/>
            {inc.rapport && !selectionMode && (
              <DeleteButtonInc onConfirm={() => onDelete?.(inc)} />
            )}
          </div>
        </div>
        <div style={{ display:'flex', gap:12, fontSize:10, fontFamily:'JetBrains Mono,monospace', color:C.muted, paddingLeft:20 }}>
          <span>{inc.timestamp?.slice(0,19).replace('T',' ')}</span>
          {inc.score > 0 && <span style={{ color:C.blue }}>AI {inc.score?.toFixed(3)}</span>}
          {(inc.anomalies?.length || 0) > 1 && <span style={{ color:C.orange }}>{inc.anomalies.length} anomalies</span>}
          {inc.rapport && <span style={{ color:C.yellow }}>📄 report</span>}
        </div>
      </div>
    </div>
  )
}

export function PageIncidents({ incidents, historyTotal = 0, historyOffset = 0, loadingMore = false, onLoadMore = () => {} }) {
  const [selTimestamp, setSelTimestamp] = useState(null)
  const [reportContent, setReportContent] = useState(null)
  const [loadingReport, setLoadingReport] = useState(false)
  const [downloadingPdf, setDownloadingPdf] = useState(false)
  // ← AJOUT : recommendation liée à l'incident sélectionné -- répond à
  // "savoir pour chaque incident quelle est sa recommendation". Recherche
  // automatique (pas au clic) dès qu'un incident avec rapport est
  // sélectionné, via le nouvel endpoint GET /api/recommendations/
  // by-report/{nom}. null = pas encore cherché / aucune trouvée --
  // les deux cas s'affichent pareil (rien), la distinction n'a pas
  // besoin d'être visible ici.
  const [filter, setFilter]         = useState('ALL')
  const [reportOpen, setReportOpen] = useState(false)
  const [scope, setScope] = useState('today')

  // ← AJOUT : suppression -- "deletedRapports" filtre localement ce qui a
  // déjà été supprimé (identifié par nom de rapport, la seule clé stable
  // qu'un incident possède), sans avoir besoin de faire remonter l'état
  // jusqu'à App.jsx. Fonctionne quelle que soit l'origine de "incidents"
  // (WebSocket en direct ou /api/rapports), puisque le filtrage se fait
  // uniquement à l'affichage.
  const [deletedRapports, setDeletedRapports] = useState(() => new Set())
  const [selectionMode, setSelectionMode]     = useState(false)
  const [selectedRapports, setSelectedRapports] = useState(() => new Set())
  // ← AJOUT : confirmation stylée (remplace window.confirm) + attente de
  // chargement de toutes les pages avant une sélection totale réelle.
  const [confirmationEnCours, setConfirmationEnCours] = useState(false)
  const [attenteChargementPourTout, setAttenteChargementPourTout] = useState(false)

  const estAujourdhui = (timestamp) => {
    if (!timestamp) return false
    const d = new Date(timestamp), n = new Date()
    return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  }

  const incidentsVisibles = incidents.filter(inc => !inc.rapport || !deletedRapports.has(inc.rapport))
  const reversed = [...incidentsVisibles].reverse()
  const todayItems   = reversed.filter(inc => estAujourdhui(inc.timestamp))
  const historyItems = reversed.filter(inc => !estAujourdhui(inc.timestamp))
  // ← CORRIGÉ : historyTotal (prop, total serveur AVANT toute suppression
  // locale) ne bougeait jamais après une suppression -- le badge restait
  // gonflé jusqu'à un rechargement complet de la page. nbSupprimesHistory
  // compte, parmi TOUS les rapports supprimés localement, ceux qui
  // étaient dans History (pas Today, qui n'a pas ce problème -- il est
  // recalculé directement depuis incidentsVisibles à chaque rendu).
  const nbSupprimesHistory = incidents.filter(
    inc => inc.rapport && deletedRapports.has(inc.rapport) && !estAujourdhui(inc.timestamp)
  ).length
  const trueHistoryCount = Math.max(historyItems.length, historyTotal - nbSupprimesHistory - todayItems.length)
  const enScope  = scope === 'today' ? todayItems : historyItems
  const filtered = filter === 'ALL' ? enScope : enScope.filter(inc => getSeverity(inc) === filter)
  const selected = selTimestamp !== null ? (reversed.find(inc => inc.timestamp === selTimestamp) || null) : null

  const [recommandationLiee, setRecommandationLiee] = useState(null)
  useEffect(() => {
    if (!selected?.rapport) { setRecommandationLiee(null); return }
    let annule = false
    fetch(`/api/recommendations/by-report/${selected.rapport}`)
      .then(r => r.ok ? r.json() : null)
      .then(d => { if (!annule) setRecommandationLiee(d) })
      .catch(() => { if (!annule) setRecommandationLiee(null) })
    return () => { annule = true }
  }, [selected?.rapport])

  const critCount = enScope.filter(i => getSeverity(i) === 'CRITICAL').length
  const highCount = enScope.filter(i => getSeverity(i) === 'HIGH').length
  const monCount  = enScope.filter(i => getSeverity(i) === 'MONITORING').length

  const openReport = async (nom) => {
    if (!nom) return
    setLoadingReport(true); setReportOpen(true)
    try {
      const r = await fetch(`/api/rapports/${nom}`)
      if (!r.ok) throw new Error()
      const d = await r.json()
      setReportContent(d.contenu || null)
    } catch { setReportContent(null) }
    finally { setLoadingReport(false) }
  }
  const closeReport = () => { setReportOpen(false); setReportContent(null) }

  const telechargerRapport = async () => {
    if (!selected?.rapport) return
    setDownloadingPdf(true)
    try {
      const r = await fetch(`/api/rapports/${selected.rapport}/pdf`)
      if (!r.ok) throw new Error('PDF generation failed')
      const blob = await r.blob()
      const url  = URL.createObjectURL(blob)
      const a    = document.createElement('a')
      a.href = url
      a.download = selected.rapport.replace(/\.md$/, '.pdf')
      document.body.appendChild(a)
      a.click()
      document.body.removeChild(a)
      URL.revokeObjectURL(url)
    } catch {
      alert('PDF download failed — check that fpdf2 and the DejaVu fonts are installed on the backend.')
    } finally {
      setDownloadingPdf(false)
    }
  }

  const deleteOneIncident = (inc) => {
    if (!inc?.rapport) return
    setDeletedRapports(prev => new Set(prev).add(inc.rapport))
    if (selTimestamp === inc.timestamp) { setSelTimestamp(null); closeReport() }
    fetch(`/api/rapports/${inc.rapport}`, { method: 'DELETE' }).catch(() => {})
  }

  const toggleSelectInc = (inc) => {
    if (!inc?.rapport) return
    setSelectedRapports(prev => {
      const suivant = new Set(prev)
      if (suivant.has(inc.rapport)) suivant.delete(inc.rapport)
      else suivant.add(inc.rapport)
      return suivant
    })
  }

  // ← AJOUT : sélectionne tout ce qui est visible (filtré par sévérité +
  // onglet Today/History courant) -- seuls les incidents avec un rapport
  // comptent, les autres n'ont rien à sélectionner (voir toggleSelectInc).
  // Basé sur "filtered", donc respecte déjà le filtre CRITICAL/HIGH/
  // MONITORING actif au moment du clic.
  const rapportsVisibles = filtered.map(inc => inc.rapport).filter(Boolean)
  const toutSelectionneInc = rapportsVisibles.length > 0 && rapportsVisibles.every(r => selectedRapports.has(r))

  // ← AJOUT : History est paginé (Load more) -- "filtered" ne contient
  // que ce qui a déjà été chargé, jamais tout le total serveur tant que
  // "Load more" n'a pas été cliqué assez de fois. Sans ça, "Select all"
  // ne sélectionnait que la première page chargée (ex: 113 sur 863
  // réels) sans que rien n'indique que ce n'était qu'un sous-ensemble.
  // Cet effet charge automatiquement toutes les pages restantes avant de
  // sélectionner réellement tout, en réagissant aux changements de
  // historyOffset/loadingMore plutôt qu'en dépendant d'un onLoadMore
  // qu'on pourrait ou non pouvoir attendre (await) directement.
  useEffect(() => {
    if (!attenteChargementPourTout) return
    if (loadingMore) return
    if (scope === 'history' && historyOffset < historyTotal) {
      onLoadMore()
    } else {
      setAttenteChargementPourTout(false)
      setSelectedRapports(new Set(rapportsVisibles))
    }
  }, [attenteChargementPourTout, loadingMore, historyOffset, historyTotal, scope])

  const selectionnerToutInc = () => {
    if (toutSelectionneInc) { setSelectedRapports(new Set()); return }
    if (scope === 'history' && historyOffset < historyTotal) {
      setAttenteChargementPourTout(true)  // déclenche l'effet ci-dessus
    } else {
      setSelectedRapports(new Set(rapportsVisibles))
    }
  }

  // ← MODIFIÉ : window.confirm() retiré -- ouvre la modale stylée
  // (ConfirmModal) à la place ; la suppression réelle est dans
  // confirmerSuppressionInc, appelée par le bouton "Delete" de la modale.
  const deleteSelectionInc = () => {
    if (selectedRapports.size === 0) return
    setConfirmationEnCours(true)
  }

  // ← CORRIGÉ : avant, TOUS les ids sélectionnés étaient retirés de
  // l'affichage IMMÉDIATEMENT (setDeletedRapports), puis TOUS les appels
  // DELETE partaient EN MÊME TEMPS, sans aucune limite de concurrence ni
  // suivi de succès -- pour 600 éléments, ça veut dire 600 requêtes HTTP
  // simultanées vers un unique processus Uvicorn, dont une bonne partie
  // échouait silencieusement (.catch(() => {}) avalait l'erreur sans rien
  // faire) tout en étant déjà affichée comme supprimée. D'où l'écart
  // observé après rafraîchissement (le fichier n'avait en réalité jamais
  // été supprimé côté serveur, seul l'affichage local mentait). Traite
  // maintenant par lots de 8 max en parallèle (Promise.allSettled),
  // attend chaque lot avant de lancer le suivant, et ne retire de
  // l'affichage QUE ce qui a un status HTTP réellement OK -- un échec
  // reste visible, honnêtement, plutôt que de disparaître à tort.
  const [suppressionEnCours, setSuppressionEnCours] = useState(false)
  const [progressionSuppression, setProgressionSuppression] = useState({ fait: 0, total: 0 })

  // ← RENFORCÉ (après un vrai retour terrain : 8 en parallèle restait
  // trop pour un unique processus Uvicorn qui fait AUSSI tourner le
  // cycle de surveillance, les appels Groq/Tavily etc. en tâche de fond
  // -- 27 échecs sur 120 constatés en pratique malgré le premier
  // correctif). Deux changements : lots réduits de 8 à 4, ET surtout,
  // chaque suppression qui échoue est maintenant retentée automatiquement
  // jusqu'à 3 fois avec un délai croissant (300ms, 600ms) avant d'être
  // vraiment comptée comme un échec -- la plupart des échecs observés
  // sont transitoires (serveur momentanément occupé), pas permanents,
  // donc se corrigent tout seuls sans que l'utilisateur ait besoin de
  // resélectionner et recliquer manuellement.
  const supprimerAvecRetry = async (url, maxTentatives = 3) => {
    for (let tentative = 1; tentative <= maxTentatives; tentative++) {
      try {
        const r = await fetch(url, { method: 'DELETE' })
        if (r.ok) return true
      } catch (e) { /* reseau -- retenter */ }
      if (tentative < maxTentatives) await new Promise(res => setTimeout(res, 300 * tentative))
    }
    return false
  }

  const confirmerSuppressionInc = async () => {
    setConfirmationEnCours(false)
    const aTraiter = [...selectedRapports]
    setSuppressionEnCours(true)
    setProgressionSuppression({ fait: 0, total: aTraiter.length })
    if (selected?.rapport && selectedRapports.has(selected.rapport)) { setSelTimestamp(null); closeReport() }

    const TAILLE_LOT = 4
    const reussis = []
    for (let i = 0; i < aTraiter.length; i += TAILLE_LOT) {
      const lot = aTraiter.slice(i, i + TAILLE_LOT)
      const resultats = await Promise.allSettled(
        lot.map(async nom => ({ nom, ok: await supprimerAvecRetry(`/api/rapports/${nom}`) }))
      )
      resultats.forEach(r => {
        if (r.status === 'fulfilled' && r.value.ok) reussis.push(r.value.nom)
      })
      setProgressionSuppression({ fait: Math.min(i + TAILLE_LOT, aTraiter.length), total: aTraiter.length })
      // ← seuls les succès confirmés jusqu'ici disparaissent de
      // l'affichage, lot par lot -- jamais tout d'un coup en supposant
      // que ça va marcher.
      setDeletedRapports(prev => {
        const suivant = new Set(prev)
        reussis.forEach(nom => suivant.add(nom))
        return suivant
      })
      // ← pause entre les lots -- laisse respirer le serveur, qui fait
      // aussi tourner la surveillance en tâche de fond pendant ce temps.
      if (i + TAILLE_LOT < aTraiter.length) await new Promise(r => setTimeout(r, 150))
    }

    setSuppressionEnCours(false)
    const echecs = aTraiter.length - reussis.length
    if (echecs > 0) {
      alert(`${echecs} sur ${aTraiter.length} suppressions ont échoué même après plusieurs tentatives -- réessaie de les sélectionner et supprimer à nouveau.`)
    }
    setSelectedRapports(new Set())
    setSelectionMode(false)
  }

  const changerScope = (id) => {
    setScope(id); setSelTimestamp(null); closeReport()
    setSelectionMode(false); setSelectedRapports(new Set())
  }
  const changerFiltre = (f) => {
    setFilter(f); setSelTimestamp(null); closeReport()
    setSelectionMode(false); setSelectedRapports(new Set())
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div style={{ display:'flex', gap:12, alignItems:'flex-start' }}>
          {/* ← RETIRÉ : pastille dégradée bleu/cyan devant le titre. Le
              titre porte maintenant lui-même le dégradé, ce qui évite de
              répéter deux fois le même accent côte à côte. */}
          <div>
            <h2 style={{ fontSize:22, fontWeight:800, marginBottom:6, letterSpacing:'-0.02em',
                         background:'linear-gradient(180deg, #ffffff 0%, #b9c9dd 130%)',
                         WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent' }}>Incident Management</h2>
            <div style={{ fontSize:13, color:C.sub }}>AI-detected infrastructure events · Click any incident to view analysis</div>
          </div>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          {critCount > 0 && <Chip label={`${critCount} CRITICAL`}  color={C.red}/>}
          {highCount > 0 && <Chip label={`${highCount} HIGH`}      color={C.orange}/>}
          {monCount  > 0 && <Chip label={`${monCount} MONITORING`} color={C.yellow}/>}
          {enScope.length === 0 && <Chip label="ALL CLEAR" color={C.green}/>}
        </div>
      </div>

      {incidentsVisibles.length === 0 ? (
        <Card style={{ padding:'60px 0', textAlign:'center' }}>
          <div style={{ fontSize:40, marginBottom:16 }}>✓</div>
          <div style={{ fontSize:15, color:C.green, fontWeight:700, marginBottom:6 }}>All Systems Operational</div>
          <div style={{ fontSize:12, color:C.muted }}>No anomalies detected by the monitoring engine</div>
        </Card>
      ) : (
        <>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:10 }}>
            {/* ← MODIFIÉ : même style que TabButton (PageRecommendations.jsx)
                -- boîte bordée, bleue quand actif, "(N)" entre parenthèses
                dans le libellé au lieu d'une puce arrondie séparée. Même
                composant visuel des deux côtés, sur demande explicite de
                cohérence entre les deux pages. */}
            <div style={{ display:'flex', gap:8 }}>
              <button onClick={()=>changerScope('today')}
                style={{ padding:'7px 16px', borderRadius:8, border:`1px solid ${scope==='today'?C.blue:C.border}`,
                         background: scope==='today' ? C.blue+'18' : 'transparent', color: scope==='today' ? C.blue : C.sub,
                         fontSize:12, fontWeight:700, cursor:'pointer', fontFamily:'JetBrains Mono, monospace', transition:'all 0.15s' }}>
                Today {todayItems.length > 0 ? `(${todayItems.length})` : ''}
              </button>
              <button onClick={()=>changerScope('history')}
                style={{ padding:'7px 16px', borderRadius:8, border:`1px solid ${scope==='history'?C.blue:C.border}`,
                         background: scope==='history' ? C.blue+'18' : 'transparent', color: scope==='history' ? C.blue : C.sub,
                         fontSize:12, fontWeight:700, cursor:'pointer', fontFamily:'JetBrains Mono, monospace', transition:'all 0.15s' }}>
                History {trueHistoryCount > 0 ? `(${trueHistoryCount})` : ''}
              </button>
            </div>

            <div style={{ display:'flex', gap:10, alignItems:'center' }}>
              <div style={{ display:'flex', gap:6, background:C.surface, borderRadius:8, padding:4, border:`1px solid ${C.border}` }}>
                {['ALL','CRITICAL','HIGH','MONITORING'].map(f => (
                  <button key={f} onClick={()=>changerFiltre(f)}
                    style={{ padding:'5px 14px', borderRadius:6, fontSize:11, fontFamily:'JetBrains Mono,monospace', fontWeight:700, border:'none', cursor:'pointer',
                      background: filter===f ? (f==='CRITICAL'?C.red:f==='HIGH'?C.orange:f==='MONITORING'?C.yellow:C.blue) : 'transparent',
                      color: filter===f ? '#fff' : C.sub, transition:'all 0.15s' }}>
                    {f}
                  </button>
                ))}
              </div>

              {filtered.length > 0 && (
                <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                  {selectionMode && (
                    <button onClick={selectionnerToutInc} disabled={attenteChargementPourTout}
                      style={{ background:'transparent', border:`1px solid ${C.border}`, borderRadius:6,
                               color:C.sub, fontSize:11, padding:'5px 12px', cursor: attenteChargementPourTout ? 'default' : 'pointer',
                               opacity: attenteChargementPourTout ? 0.6 : 1,
                               fontFamily:'JetBrains Mono,monospace' }}>
                      {attenteChargementPourTout ? '⟳ Loading all…' : toutSelectionneInc ? 'Deselect all' : 'Select all'}
                    </button>
                  )}
                  {suppressionEnCours && (
                    <span style={{ fontSize:12, color:C.orange, fontFamily:'JetBrains Mono,monospace' }}>
                      ⟳ Deleting {progressionSuppression.fait}/{progressionSuppression.total}…
                    </span>
                  )}
                  {selectionMode && selectedRapports.size > 0 && !suppressionEnCours && (
                    <>
                      <span style={{ fontSize:12, color:C.sub, fontFamily:'JetBrains Mono,monospace' }}>
                        {selectedRapports.size} selected
                      </span>
                      <button onClick={deleteSelectionInc}
                        style={{ background:'transparent', border:`1px solid ${C.red}60`, borderRadius:6,
                                 color:C.red, fontSize:11, padding:'5px 12px', cursor:'pointer',
                                 fontFamily:'JetBrains Mono,monospace' }}>
                        🗑 Delete selected
                      </button>
                    </>
                  )}
                  <button
                    onClick={() => { setSelectionMode(v => !v); setSelectedRapports(new Set()) }}
                    style={{ background: selectionMode ? 'transparent' : C.blue+'12',
                             border:`1px solid ${selectionMode ? C.border : C.blue+'50'}`, borderRadius:6,
                             color: selectionMode ? C.sub : C.blue, fontSize:11, padding:'5px 12px', cursor:'pointer',
                             fontFamily:'JetBrains Mono,monospace', transition:'all 0.15s' }}>
                    {selectionMode ? 'Cancel' : 'Select'}
                  </button>
                </div>
              )}
            </div>
          </div>

          <div style={{ display:'grid', gridTemplateColumns: selected ? '360px 1fr' : '1fr', gap:16, alignItems:'start' }}>
            <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
              {filtered.length === 0 && (
                <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'30px 0' }}>
                  {scope === 'today'
                    ? <>No incidents today. <span style={{color:C.blue,cursor:'pointer',fontWeight:600}} onClick={()=>changerScope('history')}>View history →</span></>
                    : 'No incidents for this filter'}
                </div>
              )}
              {scope === 'history' ? (
                Object.entries(
                  filtered.reduce((groupes, inc) => {
                    const d = inc.timestamp ? new Date(inc.timestamp) : null
                    const hier = new Date(); hier.setDate(hier.getDate()-1)
                    let cle = 'Unknown date'
                    if (d) {
                      if (d.getFullYear()===hier.getFullYear() && d.getMonth()===hier.getMonth() && d.getDate()===hier.getDate()) cle = 'Yesterday'
                      else cle = d.toLocaleDateString('en-US', { weekday:'short', day:'2-digit', month:'short', year:'numeric' })
                    }
                    ;(groupes[cle] = groupes[cle] || []).push(inc)
                    return groupes
                  }, {})
                ).map(([dateLabel, items]) => (
                  <div key={dateLabel}>
                    <div style={{ fontSize:10, fontWeight:700, color:C.muted, letterSpacing:'0.1em', fontFamily:'JetBrains Mono,monospace', margin:'10px 0 6px', paddingLeft:2 }}>
                      {dateLabel.toUpperCase()} · {items.length}
                    </div>
                    <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
                      {items.map((inc, i) => (
                        <IncidentCard key={i} inc={inc} selTimestamp={selTimestamp} setSelTimestamp={setSelTimestamp} closeReport={closeReport}
                          onDelete={deleteOneIncident} selectionMode={selectionMode} selected={selectedRapports.has(inc.rapport)} onToggleSelect={toggleSelectInc}/>
                      ))}
                    </div>
                  </div>
                ))
              ) : (
                filtered.map((inc, i) => (
                  <IncidentCard key={i} inc={inc} selTimestamp={selTimestamp} setSelTimestamp={setSelTimestamp} closeReport={closeReport}
                    onDelete={deleteOneIncident} selectionMode={selectionMode} selected={selectedRapports.has(inc.rapport)} onToggleSelect={toggleSelectInc}/>
                ))
              )}

              {scope === 'history' && historyOffset < historyTotal && (
                <button onClick={onLoadMore} disabled={loadingMore}
                  style={{ marginTop:8, padding:'10px 16px', borderRadius:8, border:`1px solid ${C.border}`, background:C.surface, color:C.sub, fontSize:12, fontFamily:'JetBrains Mono,monospace', fontWeight:700, cursor: loadingMore?'default':'pointer', opacity: loadingMore?0.6:1, textAlign:'center' }}>
                  {loadingMore ? '⟳ Loading…' : `Load more history (${historyTotal - historyOffset} older)`}
                </button>
              )}
            </div>

            {selected && (
              <div style={{ display:'flex', flexDirection:'column', gap:12, position:'sticky', top:16 }}>
                <Card style={{ padding:'18px 20px', borderLeft:`3px solid ${severityColor(getSeverity(selected))}` }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14 }}>
                    <div>
                      <div style={{ fontSize:15, fontWeight:700, color:C.text, marginBottom:3 }}>Incident Details</div>
                      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace' }}>
                        {selected.timestamp?.slice(0,19).replace('T',' ')}
                        {selected.score > 0 && <span style={{ marginLeft:12, color:C.blue }}>AI {selected.score?.toFixed(4)}</span>}
                      </div>
                    </div>
                    <div style={{ display:'flex', gap:8, alignItems:'center' }}>
                      <Chip label={getSeverity(selected)} color={severityColor(getSeverity(selected))}/>
                      {selected.rapport && <DeleteButtonInc onConfirm={() => deleteOneIncident(selected)} />}
                    </div>
                  </div>
                  <div style={{ borderTop:`1px solid ${C.border}`, paddingTop:14 }}>
                    {selected.structured ? (
                      <StructuredIncident structured={selected.structured}/>
                    ) : (
                      <div style={{ fontSize:13, color:C.sub, lineHeight:1.7 }}>{selected.message}</div>
                    )}

                    {/* ← AJOUT : recommendation liée à cet incident --
                        répond à "savoir pour chaque incident quelle est sa
                        recommendation". N'affiche rien si aucune trouvée
                        (incident sans recommendation associée, ex: rapport
                        dégradé généré pendant une panne DB) -- pas un
                        message d'erreur, juste une absence silencieuse. */}
                    {recommandationLiee && (
                      <div style={{ marginTop:14, paddingTop:14, borderTop:`1px solid ${C.border}` }}>
                        <div style={{ fontSize:10, fontWeight:700, color:C.sub, letterSpacing:'0.08em', marginBottom:8, fontFamily:'JetBrains Mono,monospace' }}>
                          LINKED RECOMMENDATION
                        </div>
                        <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', gap:10, padding:'10px 12px', background:C.surface, border:`1px solid ${C.border}`, borderRadius:8 }}>
                          <div style={{ fontSize:12.5, color:C.text, fontWeight:600 }}>{recommandationLiee.title}</div>
                          <span style={{ fontSize:10, fontWeight:700, padding:'2px 9px', borderRadius:10, flexShrink:0,
                                         color: recommandationLiee.status === 'RESOLVED' ? C.green : C.orange,
                                         border:`1px solid ${recommandationLiee.status === 'RESOLVED' ? C.green : C.orange}` }}>
                            {recommandationLiee.status === 'RESOLVED' ? 'RESOLVED' : 'ACTIVE'}
                          </span>
                        </div>
                      </div>
                    )}

                    {(selected.anomalies?.length || 0) > 1 && (() => {
                      const { crit, haute, autre } = grouperParSeverite(selected.anomalies)
                      return (
                        <div style={{ marginTop:16, paddingTop:14, borderTop:`1px solid ${C.border}` }}>
                          <div style={{ fontSize:11, fontWeight:700, color:C.sub, letterSpacing:'0.08em', marginBottom:10, fontFamily:'JetBrains Mono,monospace' }}>
                            ALL DETECTED ANOMALIES ({selected.anomalies.length})
                          </div>
                          <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
                            {crit.length > 0 && (
                              <div style={{ background:'#ef444410', border:'1px solid #ef444430', borderRadius:8, padding:'10px 14px' }}>
                                <div style={{ fontSize:10, fontWeight:700, color:C.red, marginBottom:6, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em' }}>● CRITICAL</div>
                                {crit.map((a,i) => <div key={i} style={{ fontSize:12, color:C.sub, lineHeight:1.7 }}>▸ {a.message}</div>)}
                              </div>
                            )}
                            {haute.length > 0 && (
                              <div style={{ background:'#f9731610', border:'1px solid #f9731630', borderRadius:8, padding:'10px 14px' }}>
                                <div style={{ fontSize:10, fontWeight:700, color:C.orange, marginBottom:6, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em' }}>● HIGH</div>
                                {haute.map((a,i) => <div key={i} style={{ fontSize:12, color:C.sub, lineHeight:1.7 }}>▸ {a.message}</div>)}
                              </div>
                            )}
                            {autre.length > 0 && (
                              <div style={{ background:'#eab30810', border:'1px solid #eab30830', borderRadius:8, padding:'10px 14px' }}>
                                <div style={{ fontSize:10, fontWeight:700, color:C.yellow, marginBottom:6, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em' }}>● MONITORING</div>
                                {autre.map((a,i) => <div key={i} style={{ fontSize:12, color:C.sub, lineHeight:1.7 }}>▸ {a.message}</div>)}
                              </div>
                            )}
                          </div>
                        </div>
                      )
                    })()}
                  </div>
                </Card>

                {selected.rapport && (
                  <Card style={{ padding:'14px 18px' }}>
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                      <div>
                        <div style={{ fontSize:12, fontWeight:600, color:C.yellow, marginBottom:3 }}>📄 Full Incident Report</div>
                        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace' }}>{selected.rapport}</div>
                      </div>
                      <div style={{ display:'flex', gap:8, flexShrink:0 }}>
                        {reportOpen && reportContent && (
                          <button onClick={telechargerRapport} disabled={downloadingPdf}
                            style={{ padding:'7px 16px', borderRadius:7, fontSize:12, fontWeight:600, fontFamily:'JetBrains Mono,monospace', cursor: downloadingPdf?'default':'pointer', border:`1px solid ${C.blue}60`, background:C.bg, color:C.blue, transition:'all 0.15s', opacity: downloadingPdf?0.6:1 }}>
                            {downloadingPdf ? '⟳ Generating PDF...' : '⬇ Download PDF'}
                          </button>
                        )}
                        <button onClick={()=>reportOpen?closeReport():openReport(selected.rapport)}
                          style={{ padding:'7px 16px', borderRadius:7, fontSize:12, fontWeight:600, fontFamily:'JetBrains Mono,monospace', cursor:'pointer', border:`1px solid ${C.yellow}60`, background:reportOpen?C.yellow+'20':C.bg, color:C.yellow, transition:'all 0.15s' }}>
                          {loadingReport ? '⟳' : reportOpen ? '✕ Close' : '↓ Open report'}
                        </button>
                      </div>
                    </div>
                  </Card>
                )}

                {reportOpen && (
                  <Card style={{ padding:0, overflow:'hidden', border:`1px solid ${C.borderHi}` }}>
                    <div style={{ maxHeight:'75vh', overflowY:'auto' }}>
                      {loadingReport ? (
                        <div style={{ padding:'40px 0', textAlign:'center', color:C.muted, fontFamily:'JetBrains Mono,monospace', fontSize:12 }}>⟳ Loading...</div>
                      ) : reportContent ? (
                        <ReportViewer content={reportContent} reportName={selected.rapport}/>
                      ) : (
                        <div style={{ padding:'30px 20px', color:C.red, fontSize:12, fontFamily:'JetBrains Mono,monospace' }}>
                          ✗ Report not found — may have been deleted or not yet generated.
                        </div>
                      )}
                    </div>
                  </Card>
                )}
              </div>
            )}
          </div>
        </>
      )}

      {confirmationEnCours && (
        <ConfirmModal
          message={`Delete ${selectedRapports.size} selected incident${selectedRapports.size > 1 ? 's' : ''}? This cannot be undone.`}
          onConfirm={confirmerSuppressionInc}
          onCancel={() => setConfirmationEnCours(false)}
        />
      )}
    </div>
  )
}