import { useState, useEffect } from 'react'
import { C } from '../utils/colors'
import { severityColor, normalizeSeverity } from '../styles/theme'
import { Card } from '../components/Card'
import { Chip } from '../components/Common'

function getSeverity(inc) {
  const raw = inc.structured?.severity || inc.anomalies?.[0]?.niveau || inc.statut || inc.niveau || ''
  return normalizeSeverity(raw) || 'MONITORING'
}

// Regroupe les anomalies brutes par sévérité -- même logique que
// report_writer.py (section "Detected Anomalies" du rapport .md), pour
// que cette même vue existe aussi côté vivant, pas seulement dans le
// document persistant.
function grouperParSeverite(anomalies = []) {
  const crit  = anomalies.filter(a => String(a.niveau||'').toUpperCase().includes('CRIT'))
  const haute = anomalies.filter(a => String(a.niveau||'').toUpperCase().includes('IMP') && !crit.includes(a))
  const autre = anomalies.filter(a => !crit.includes(a) && !haute.includes(a))
  return { crit, haute, autre }
}
const SEV_ICON = { CRITICAL:'🔴', HIGH:'🟠', MONITORING:'🟡' }
const PHASE_LABEL = { immediate: 'IMMEDIATE', short_term: 'SHORT TERM', long_term: 'LONG TERM' }

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

// ── Rendu direct depuis le JSON structuré -- ni découpage de texte, ni
// regex sur des marqueurs ---INCIDENT---/---RECOMMENDATION---. Miroir de
// RecoCard (PageRecommendations.jsx) en plus simple : lecture seule, pas
// de bouton "Accepter & Exécuter" ici -- l'exécution reste centralisée sur
// la page Recommendations, cette page reste un historique/diagnostic.
// Boutons Accept/Reject -- dupliqué depuis PageRecommendations.jsx
// volontairement (pas de couche de composants partagés établie entre les
// deux pages pour ce composant précis) -- même comportement : ne fait
// jamais confiance au texte libre du LLM, seulement à action_id/
// action_params déjà validés côté backend.
function ActionButton({ actionId, params, risk }) {
  const [status, setStatus] = useState('idle')
  const [message, setMessage] = useState('')
  const riskColor = risk === 'low' ? C.green : risk === 'medium' ? C.orange : C.red

  const execute = async () => {
    setStatus('loading')
    try {
      const r = await fetch('/api/actions/execute', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ action_id: actionId, params }),
      })
      const data = await r.json()
      setStatus(data.ok ? 'success' : 'error')
      setMessage(data.message || data.error || '')
    } catch { setStatus('error'); setMessage('Erreur réseau') }
  }

  if (status === 'success') return <div style={{ padding:'6px 12px', borderRadius:6, background:'#22c55e15', border:'1px solid #22c55e30', fontSize:11, color:C.green, marginTop:6 }}>{message}</div>
  if (status === 'error')   return <div style={{ padding:'6px 12px', borderRadius:6, background:'#ef444415', border:'1px solid #ef444430', fontSize:11, color:C.red, marginTop:6 }}>✗ {message}</div>
  if (status === 'rejected') return <div style={{ fontSize:10, color:C.muted, marginTop:4, fontStyle:'italic' }}>Refusé — aucune modification effectuée</div>

  return (
    <div style={{ display:'flex', alignItems:'center', gap:8, marginTop:6 }}>
      <span style={{ fontSize:10, color:riskColor, fontWeight:600 }}>
        {risk === 'low' ? '🟢 Risque faible' : risk === 'medium' ? '🟠 Risque moyen' : '🔴 Risque élevé'}
      </span>
      <button onClick={execute} disabled={status === 'loading' || !params?.node}
        style={{ padding:'4px 12px', borderRadius:6, border:'none', background:C.green, color:'#fff', cursor:'pointer', fontSize:11, fontWeight:700, opacity:(status==='loading'||!params?.node)?0.5:1 }}>
        {status === 'loading' ? '⟳ Exécution...' : '✓ Accepter & Exécuter'}
      </button>
      <button onClick={() => setStatus('rejected')} disabled={status === 'loading'}
        style={{ padding:'4px 10px', borderRadius:6, border:`1px solid ${C.border}`, background:'transparent', color:C.muted, cursor:'pointer', fontSize:11 }}>
        ✗ Refuser
      </button>
    </div>
  )
}

function StructuredIncident({ structured, loadingFull }) {
  if (!structured || structured._parse_failed) {
    // ← MODIFIÉ : deux cas distincts, deux messages distincts -- avant,
    // "chargé depuis l'historique" (normal) et "le LLM a renvoyé un JSON
    // invalide" (rare, un vrai problème) étaient indiscernables, donnant
    // l'impression que certains incidents étaient arbitrairement "cassés".
    const depuisHistorique = structured?._source === 'history'
    return (
      <div style={{ fontSize:13, color:C.muted, lineHeight:1.6 }}>
        {depuisHistorique ? (
          <div style={{ display:'flex', gap:8, alignItems:'flex-start', padding:'8px 0' }}>
            <span style={{ color:C.blue, flexShrink:0, marginTop:1 }}>{loadingFull ? '⟳' : 'ℹ'}</span>
            <span>
              {loadingFull ? 'Loading full analysis…' : (structured._raw || 'Loaded from a saved report — open the full report below for the complete analysis.')}
            </span>
          </div>
        ) : (
          <>
            <div style={{ display:'flex', gap:8, alignItems:'flex-start', padding:'8px 10px', background:'#eab30810', border:`1px solid ${C.yellow}30`, borderRadius:7, marginBottom:10 }}>
              <span style={{ color:C.yellow, flexShrink:0 }}>⚠</span>
              <span style={{ fontSize:12, color:C.yellow }}>AI response could not be fully structured this time — showing available text.</span>
            </div>
            {structured?._raw || 'No structured analysis available for this incident.'}
          </>
        )}
      </div>
    )
  }
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
      <div>
        <div style={{ fontSize:13, color:C.text, lineHeight:1.6, marginBottom:8 }}>{structured.summary}</div>
        {(structured.causes || []).length > 0 && (
          <TextBlock text={structured.causes.map(c => `- ${c}`).join('\n')}/>
        )}
      </div>

      {structured.warning && (
        <div style={{ padding:'10px 14px', background:'#ef444410', border:`1px solid ${C.red}35`, borderRadius:7, display:'flex', gap:10, alignItems:'flex-start' }}>
          <span style={{ fontSize:14, color:C.red, flexShrink:0 }}>⚠</span>
          <span style={{ fontSize:12, color:C.red, fontWeight:600, lineHeight:1.5 }}>{structured.warning}</span>
        </div>
      )}

      {(structured.steps || []).length > 0 && (
        <div style={{ background:'#0a1a0e', border:`1px solid ${C.green}25`, borderRadius:8, padding:'12px 14px' }}>
          <div style={{ fontSize:11, fontWeight:700, color:C.green, letterSpacing:'0.1em', marginBottom:10, fontFamily:'JetBrains Mono,monospace' }}>
            RECOMMENDED ACTIONS
          </div>
          <div style={{ display:'flex', flexDirection:'column', gap:10 }}>
            {structured.steps.map((step, i) => (
              <div key={i}>
                <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:4 }}>
                  <span style={{ fontSize:9, fontWeight:700, color:C.blue, background:C.blue+'18', border:`1px solid ${C.blue}40`, borderRadius:8, padding:'1px 7px', fontFamily:'JetBrains Mono,monospace' }}>
                    {PHASE_LABEL[step.phase] || (step.phase||'').toUpperCase()}
                  </span>
                  <span style={{ fontSize:13, color:C.sub }}>{step.action}</span>
                </div>
                {step.command && <CodeBlock code={step.command}/>}
                {/* ← AJOUT : bouton d'action fonctionnel -- disponible dès
                    que action_id/action_params sont présents, que
                    l'incident soit en direct ou récupéré à la demande
                    depuis le JSON compagnon persisté. */}
                {step.action_id && step.action_params && (
                  <ActionButton actionId={step.action_id} params={step.action_params} risk={step.risk || 'medium'}/>
                )}
              </div>
            ))}
          </div>
        </div>
      )}
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
      {/* Header */}
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

      {/* Sections */}
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

      {/* Footer */}
      <div style={{ padding:'12px 24px', background:'#040810', borderTop:`1px solid ${C.border}`, display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div style={{ fontSize:11, color:C.muted }}>OpsPilot Infrastructure AI — Auto-generated incident report</div>
        <div style={{ fontSize:10, color:C.border, fontFamily:'JetBrains Mono,monospace' }}>Do not reply · Open dashboard for live status</div>
      </div>
    </div>
  )
}

// Carte d'incident dans la liste -- extraite en composant partagé car
// utilisée à la fois par la vue Today (liste plate) et History (regroupée
// par date), pour ne pas dupliquer le JSX entre les deux.
// ← CORRIGÉ : sélection par inc.timestamp (identité stable), plus par
// index de position dans le tableau -- l'index se décalait silencieusement
// dès qu'un nouvel incident arrivait en direct pendant qu'un autre était
// sélectionné (tout glisse d'un cran), faisant pointer la sélection vers
// un incident différent de celui réellement cliqué.
function IncidentCard({ inc, selTimestamp, setSelTimestamp, closeReport }) {
  const sev      = getSeverity(inc)
  const sevColor = severityColor(sev)
  const isActive = selTimestamp === inc.timestamp
  return (
    <div onClick={() => { setSelTimestamp(isActive ? null : inc.timestamp); closeReport() }}
      style={{ background: isActive ? C.surface : C.card, border:`1px solid ${isActive?sevColor+'70':C.border}`, borderLeft:`3px solid ${sevColor}`, borderRadius:8, padding:'12px 14px', cursor:'pointer', transition:'all 0.15s' }}
      onMouseEnter={e=>{ if(!isActive) { e.currentTarget.style.background=C.surface; e.currentTarget.style.borderColor=sevColor+'40' }}}
      onMouseLeave={e=>{ if(!isActive) { e.currentTarget.style.background=C.card;    e.currentTarget.style.borderColor=C.border }}}
    >
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8, marginBottom:6 }}>
        <div style={{ display:'flex', gap:8, alignItems:'flex-start', flex:1 }}>
          <span style={{ fontSize:12, flexShrink:0, marginTop:2 }}>{SEV_ICON[sev]||'⚪'}</span>
          <span style={{ fontSize:14, color:C.text, fontWeight:700, lineHeight:1.4 }}>
            {inc.structured?.fix_title || inc.structured?.summary || inc.anomalies?.[0]?.message || (inc.rapport ? 'Historical incident' : 'Anomaly detected')}
          </span>
        </div>
        <Chip label={sev} color={sevColor}/>
      </div>
      <div style={{ display:'flex', gap:12, fontSize:10, fontFamily:'JetBrains Mono,monospace', color:C.muted, paddingLeft:20 }}>
        <span>{inc.timestamp?.slice(0,19).replace('T',' ')}</span>
        {inc.score > 0 && <span style={{ color:C.purple }}>AI {inc.score?.toFixed(3)}</span>}
        {(inc.anomalies?.length || 0) > 1 && <span style={{ color:C.orange }}>{inc.anomalies.length} anomalies</span>}
        {inc.rapport && <span style={{ color:C.yellow }}>📄 report</span>}
      </div>
    </div>
  )
}

export function PageIncidents({ incidents, historyTotal = 0, historyOffset = 0, loadingMore = false, onLoadMore = () => {} }) {
  // ← CORRIGÉ : sélection par timestamp (identité stable de l'incident),
  // plus par index de position -- voir commentaire détaillé sur
  // IncidentCard plus haut.
  const [selTimestamp, setSelTimestamp] = useState(null)
  const [reportContent, setReportContent] = useState(null)
  const [loadingReport, setLoadingReport] = useState(false)
  const [downloadingPdf, setDownloadingPdf] = useState(false)
  const [filter, setFilter]         = useState('ALL')
  const [reportOpen, setReportOpen] = useState(false)
  // ← AJOUT : cache local {nom_rapport: structured_complet} -- rempli à la
  // demande (voir useEffect plus bas) quand on sélectionne un incident qui
  // n'a que le résumé (_source:'history'). Une fois récupéré, reste en
  // cache pour le reste de la session -- pas de re-fetch si on resélectionne
  // le même incident.
  const [structuredCache, setStructuredCache] = useState({})
  const [loadingFull, setLoadingFull] = useState(false)
  // ← AJOUT : scope temporel -- "today" est la vue par défaut (l'attention
  // immédiate), "history" ouvre tout ce qui est persisté. Avant, la page
  // affichait toujours tout d'un coup -- gérable à la main quand
  // l'historique n'existait pas encore, plus maintenant qu'il est chargé
  // au démarrage (voir App.jsx) et peut couvrir des jours entiers.
  const [scope, setScope] = useState('today')

  const estAujourdhui = (timestamp) => {
    if (!timestamp) return false
    const d = new Date(timestamp), n = new Date()
    return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  }

  const reversed = [...incidents].reverse()
  // ← MODIFIÉ : Today et History sont maintenant deux partitions
  // complémentaires, sans chevauchement -- avant, History montrait TOUT
  // (y compris les incidents d'aujourd'hui, dupliqués avec Today). History
  // ne montre désormais que les jours précédents, comme demandé.
  const todayItems   = reversed.filter(inc => estAujourdhui(inc.timestamp))
  const historyItems = reversed.filter(inc => !estAujourdhui(inc.timestamp))
  const enScope  = scope === 'today' ? todayItems : historyItems
  const filtered = filter === 'ALL' ? enScope : enScope.filter(inc => getSeverity(inc) === filter)
  const selected = selTimestamp !== null ? (reversed.find(inc => inc.timestamp === selTimestamp) || null) : null

  // ← AJOUT : récupération à la demande de l'analyse complète (causes,
  // steps, action_id, action_params) quand on sélectionne un incident qui
  // n'a que le résumé (_source:'history') -- jamais tout chargé au
  // démarrage, seulement pour l'incident réellement consulté. Le fichier
  // .json compagnon n'existe que pour les rapports générés après ce
  // correctif (voir report_writer.sauvegarder_rapport) -- 404 pour les
  // rapports plus anciens, géré silencieusement (reste sur le résumé).
  useEffect(() => {
    if (!selected?.rapport) return
    if (selected.structured?._source !== 'history') return
    if (structuredCache[selected.rapport]) return
    let annule = false
    setLoadingFull(true)
    fetch(`/api/rapports/${selected.rapport}/structured`)
      .then(r => r.ok ? r.json() : null)
      .then(data => { if (!annule && data) setStructuredCache(c => ({ ...c, [selected.rapport]: data })) })
      .catch(() => {})
      .finally(() => { if (!annule) setLoadingFull(false) })
    return () => { annule = true }
  }, [selected?.rapport, selected?.structured?._source])

  const structuredEffectif = (selected?.rapport && structuredCache[selected.rapport]) || selected?.structured

  // ← MODIFIÉ : les compteurs reflètent le scope actif (Today ou History),
  // pas toujours le total -- pour rester cohérents avec ce qui est
  // réellement affiché en dessous.
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

  // ← MODIFIÉ : télécharge maintenant un vrai PDF, via la nouvelle route
  // backend GET /api/rapports/{nom}/pdf (report_writer.generer_pdf) --
  // avant, téléchargeait le .md brut côté client. Nécessite d'ajouter
  // cette route à routes.py (voir message) et les 3 polices DejaVu dans
  // fonts/ à la racine du projet.
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

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <h2 style={{ fontSize:22, fontWeight:800, color:C.text, marginBottom:6 }}>Incident Management</h2>
          <div style={{ fontSize:13, color:C.sub }}>AI-detected infrastructure events · Click any incident to view analysis</div>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          {critCount > 0 && <Chip label={`${critCount} CRITICAL`}  color={C.red}/>}
          {highCount > 0 && <Chip label={`${highCount} HIGH`}      color={C.orange}/>}
          {monCount  > 0 && <Chip label={`${monCount} MONITORING`} color={C.yellow}/>}
          {enScope.length === 0 && <Chip label="ALL CLEAR" color={C.green}/>}
        </div>
      </div>

      {incidents.length === 0 ? (
        <Card style={{ padding:'60px 0', textAlign:'center' }}>
          <div style={{ fontSize:40, marginBottom:16 }}>✓</div>
          <div style={{ fontSize:15, color:C.green, fontWeight:700, marginBottom:6 }}>All Systems Operational</div>
          <div style={{ fontSize:12, color:C.muted }}>No anomalies detected by the monitoring engine</div>
        </Card>
      ) : (
        <>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', flexWrap:'wrap', gap:10 }}>
            {/* ← AJOUT : toggle Today / History -- Today est le scope par
                défaut (attention immédiate), History ouvre tout ce qui est
                persisté (voir App.jsx, chargé depuis /api/rapports). */}
            <div style={{ display:'flex', gap:6, background:C.surface, borderRadius:8, padding:4, border:`1px solid ${C.border}` }}>
              {[['today','Today'],['history','History']].map(([id,label]) => (
                <button key={id} onClick={()=>{ setScope(id); setSelTimestamp(null); closeReport() }}
                  style={{ padding:'5px 16px', borderRadius:6, fontSize:11, fontFamily:'JetBrains Mono,monospace', fontWeight:700, border:'none', cursor:'pointer',
                    background: scope===id ? C.blue : 'transparent', color: scope===id ? '#fff' : C.sub, transition:'all 0.15s', display:'flex', alignItems:'center', gap:6 }}>
                  {label}
                  {id==='today' && todayItems.length > 0 && (
                    <span style={{ background: scope===id?'rgba(255,255,255,0.25)':C.bg, borderRadius:8, padding:'1px 6px', fontSize:10 }}>{todayItems.length}</span>
                  )}
                  {id==='history' && (
                    <span style={{ background: scope===id?'rgba(255,255,255,0.25)':C.bg, borderRadius:8, padding:'1px 6px', fontSize:10 }}>{historyItems.length}</span>
                  )}
                </button>
              ))}
            </div>

            <div style={{ display:'flex', gap:6, background:C.surface, borderRadius:8, padding:4, border:`1px solid ${C.border}` }}>
              {['ALL','CRITICAL','HIGH','MONITORING'].map(f => (
                <button key={f} onClick={()=>{ setFilter(f); setSelTimestamp(null); closeReport() }}
                  style={{ padding:'5px 14px', borderRadius:6, fontSize:11, fontFamily:'JetBrains Mono,monospace', fontWeight:700, border:'none', cursor:'pointer',
                    background: filter===f ? (f==='CRITICAL'?C.red:f==='HIGH'?C.orange:f==='MONITORING'?C.yellow:C.blue) : 'transparent',
                    color: filter===f ? '#fff' : C.sub, transition:'all 0.15s' }}>
                  {f}
                </button>
              ))}
            </div>
          </div>

          <div style={{ display:'grid', gridTemplateColumns: selected ? '360px 1fr' : '1fr', gap:16, alignItems:'start' }}>
            <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
              {filtered.length === 0 && (
                <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'30px 0' }}>
                  {scope === 'today'
                    ? <>No incidents today. <span style={{color:C.blue,cursor:'pointer',fontWeight:600}} onClick={()=>setScope('history')}>View history →</span></>
                    : 'No incidents for this filter'}
                </div>
              )}
              {scope === 'history' ? (
                // ← Vue History : regroupée par date, avec en-têtes ---
                // "Yesterday" pour hier, la date complète sinon -- "Today"
                // ne peut plus apparaître ici : History exclut maintenant
                // Today par construction (voir historyItems plus haut).
                // Purement un habillage d'affichage -- `filtered` (l'ordre,
                // le contenu) reste la source de vérité.
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
                      {items.map((inc, i) => <IncidentCard key={i} inc={inc} selTimestamp={selTimestamp} setSelTimestamp={setSelTimestamp} closeReport={closeReport}/>)}
                    </div>
                  </div>
                ))
              ) : (
                filtered.map((inc, i) => <IncidentCard key={i} inc={inc} selTimestamp={selTimestamp} setSelTimestamp={setSelTimestamp} closeReport={closeReport}/>)
              )}

              {/* ← AJOUT : "Load more" -- après des mois d'utilisation avec
                  des milliers de rapports, tout charger d'un coup au
                  démarrage devient lent et inutile. Visible uniquement en
                  vue History, seulement s'il reste des rapports plus
                  anciens que ceux déjà chargés (historyOffset < historyTotal,
                  géré côté App.jsx). */}
              {scope === 'history' && historyOffset < historyTotal && (
                <button onClick={onLoadMore} disabled={loadingMore}
                  style={{ marginTop:8, padding:'10px 16px', borderRadius:8, border:`1px solid ${C.border}`, background:C.surface, color:C.sub, fontSize:12, fontFamily:'JetBrains Mono,monospace', fontWeight:700, cursor: loadingMore?'default':'pointer', opacity: loadingMore?0.6:1, textAlign:'center' }}>
                  {loadingMore ? '⟳ Loading…' : `Load more history (${historyTotal - historyOffset} older)`}
                </button>
              )}
            </div>

            {selected && (
              <div style={{ display:'flex', flexDirection:'column', gap:12, position:'sticky', top:16 }}>
                {/* ← CORRIGÉ : "Incident Details" reste TOUJOURS visible,
                    même quand le rapport complet est ouvert -- avant, un
                    {!reportOpen && ...} le cachait dès l'ouverture du
                    rapport puis le réaffichait à la fermeture, ce qui
                    donnait l'impression d'un vrai bug (disparition/
                    réapparition) plutôt qu'un choix d'affichage délibéré. */}
                <Card style={{ padding:'18px 20px', borderLeft:`3px solid ${severityColor(getSeverity(selected))}` }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:14 }}>
                    <div>
                      <div style={{ fontSize:15, fontWeight:700, color:C.text, marginBottom:3 }}>Incident Details</div>
                      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace' }}>
                        {selected.timestamp?.slice(0,19).replace('T',' ')}
                        {selected.score > 0 && <span style={{ marginLeft:12, color:C.purple }}>AI {selected.score?.toFixed(4)}</span>}
                      </div>
                    </div>
                    <Chip label={getSeverity(selected)} color={severityColor(getSeverity(selected))}/>
                  </div>
                  <div style={{ borderTop:`1px solid ${C.border}`, paddingTop:14 }}>
                    {selected.structured ? (
                      <StructuredIncident structured={structuredEffectif} loadingFull={loadingFull}/>
                    ) : (
                      <div style={{ fontSize:13, color:C.sub, lineHeight:1.7 }}>{selected.message}</div>
                    )}
                    {/* ← AJOUT : liste brute complète des anomalies détectées
                        ce cycle, groupées par sévérité -- absente de
                        Recommendations (qui ne montre que les causes
                        retenues par le LLM, pas la liste exhaustive). C'est
                        la vraie différence entre les deux pages, rendue
                        visible plutôt que seulement expliquée. */}
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
    </div>
  )
}