import { useState } from 'react'
import { C } from '../utils/colors'
import { severityColor, normalizeSeverity } from '../styles/theme'
import { Card } from '../components/Card'
import { Chip } from '../components/Common'

function getSeverity(inc) {
  const raw = inc.statut || inc.niveau || ''
  return normalizeSeverity(raw) || 'MONITORING'
}
const SEV_ICON = { CRITICAL:'🔴', HIGH:'🟠', MONITORING:'🟡' }

function cleanLLM(t = '') {
  return t
    .replace(/---INCIDENT---/g, '')
    .replace(/---RECOMMENDATION---/g, '')
    .replace(/bashcopier/g, '')
    .replace(/bash\ncopier/g, '')
    .replace(/^\*{0,2}Severity:\*{0,2}\s*(CRITICAL|HIGH|IMPORTANT|MONITORING|CRITIQUE|SURVEILLANCE)\s*$/gim, '')
    .replace(/^---\s*$/gm, '')
    .trim()
}

function parseIncidentText(text = '') {
  if (!text) return { incident:'', reco:'' }
  if (text.includes('---RECOMMENDATION---')) {
    const parts = text.split('---RECOMMENDATION---')
    return {
      incident: cleanLLM(parts[0].replace('---INCIDENT---','')),
      reco:     cleanLLM(parts[1] || '')
    }
  }
  return { incident: cleanLLM(text.replace('---INCIDENT---','')), reco:'' }
}

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
        if (s.startsWith('**') && s.endsWith('**')) {
          return <div key={i} style={{ fontSize:13, fontWeight:700, color:C.text, marginTop:4 }}
            dangerouslySetInnerHTML={{ __html: mdInline(s) }}/>
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

function cleanBashCopy(text) {
  // Pattern exact que Groq génère : ```bash\nCopy\ncommande```
  // Couvre aussi les anciens rapports .md déjà écrits sur disque
  return text
    .replace(/```bash\s*\nCopy\s*\n/g, '```bash\n')
    .replace(/```bash\s*\ncopier\s*\n/g, '```bash\n')
    // Cas où le bloc est rendu sans les backticks (parsé par sections)
    .replace(/^bash\s*\nCopy\s*\n/gm, '')
    .replace(/^bash\s*Copy\s*\n/gm, '')
    .replace(/^Copy\s*\n(ps |qm |pvecm |pvesh |iostat|sensors|top |free |df |du |ip |apt )/gm, '$1')
    .replace(/bashcopier/g, '')
    .replace(/bash\ncopier/g, '')
    .replace(/bash\nCopy\n/g, '')
    .replace(/bash\nCopy/g, '')
    .replace(/bashCopy\n/g, '')
    .replace(/bashCopy/g, '')
}

function MixedContent({ text }) {
  const cleaned = cleanBashCopy(text)

  const parts = []
  const re    = /```(\w*)\n?([\s\S]*?)```/g
  let last = 0, m
  while ((m = re.exec(cleaned)) !== null) {
    if (m.index > last) parts.push({ type:'text', content: cleaned.slice(last, m.index) })
    parts.push({ type:'code', lang: m[1]||'bash', content: m[2].trim() })
    last = m.index + m[0].length
  }
  if (last < cleaned.length) parts.push({ type:'text', content: cleaned.slice(last) })
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
                // Nœud unreachable — texte discret
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

function ReportViewer({ content, reportName }) {
  // Nettoyage : le pattern exact Groq est ```bash\nCopy\ncommande```
  const cleanContent = cleanBashCopy(content)

  const sevMatch   = cleanContent.match(/\*\*Severity:\*\*\s*(.*?)(?:\s*>|\n)/)
  const scoreMatch = cleanContent.match(/\*\*AI Score:\*\*\s*([0-9.]+)/)
  const genMatch   = cleanContent.match(/\*\*Generated:\*\*\s*(.+?)(?:\s*>|\n)/)
  const titleMatch = cleanContent.match(/^# (.+)$/m)

  const reportTitle = titleMatch?.[1] || 'Incident Report'
  const severity    = cleanContent.includes('🔴') ? 'CRITICAL' : cleanContent.includes('🟠') ? 'HIGH' : 'MONITORING'
  const sevColor    = severityColor(severity)
  const aiScore     = scoreMatch?.[1] || '—'
  const generated   = genMatch?.[1]?.trim() || '—'

  const rawSections = cleanContent.split(/^## /m).slice(1)
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
    'Thresholds Reference':        { icon:'≡', color: C.sub,    bg:'#0b0d11' },
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

export function PageIncidents({ incidents }) {
  const [sel, setSel]               = useState(null)
  const [reportContent, setReportContent] = useState(null)
  const [loadingReport, setLoadingReport] = useState(false)
  const [filter, setFilter]         = useState('ALL')
  const [reportOpen, setReportOpen] = useState(false)

  const reversed  = [...incidents].reverse()
  const filtered  = filter === 'ALL' ? reversed : reversed.filter(inc => getSeverity(inc) === filter)
  const selected  = sel !== null ? reversed[sel] : null
  const { incident: incidentText, reco: recoText } = parseIncidentText(selected?.incident)

  const critCount = incidents.filter(i => getSeverity(i) === 'CRITICAL').length
  const highCount = incidents.filter(i => getSeverity(i) === 'HIGH').length
  const monCount  = incidents.filter(i => getSeverity(i) === 'MONITORING').length

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
          {incidents.length === 0 && <Chip label="ALL CLEAR" color={C.green}/>}
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
          <div style={{ display:'flex', gap:6, background:C.surface, borderRadius:8, padding:4, alignSelf:'flex-start', border:`1px solid ${C.border}` }}>
            {['ALL','CRITICAL','HIGH','MONITORING'].map(f => (
              <button key={f} onClick={()=>{ setFilter(f); setSel(null); closeReport() }}
                style={{ padding:'5px 14px', borderRadius:6, fontSize:11, fontFamily:'JetBrains Mono,monospace', fontWeight:700, border:'none', cursor:'pointer',
                  background: filter===f ? (f==='CRITICAL'?C.red:f==='HIGH'?C.orange:f==='MONITORING'?C.yellow:C.blue) : 'transparent',
                  color: filter===f ? '#fff' : C.sub, transition:'all 0.15s' }}>
                {f}
              </button>
            ))}
          </div>

          <div style={{ display:'grid', gridTemplateColumns: selected ? '360px 1fr' : '1fr', gap:16, alignItems:'start' }}>
            <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
              {filtered.length === 0 && (
                <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'30px 0' }}>No incidents for this filter</div>
              )}
              {filtered.map((inc, i) => {
                const sev      = getSeverity(inc)
                const sevColor = severityColor(sev)
                const idx      = reversed.indexOf(inc)
                const isActive = sel === idx
                return (
                  <div key={i} onClick={() => { setSel(isActive?null:idx); closeReport() }}
                    style={{ background: isActive ? C.surface : C.card, border:`1px solid ${isActive?sevColor+'70':C.border}`, borderLeft:`3px solid ${sevColor}`, borderRadius:8, padding:'12px 14px', cursor:'pointer', transition:'all 0.15s' }}
                    onMouseEnter={e=>{ if(!isActive) { e.currentTarget.style.background=C.surface; e.currentTarget.style.borderColor=sevColor+'40' }}}
                    onMouseLeave={e=>{ if(!isActive) { e.currentTarget.style.background=C.card;    e.currentTarget.style.borderColor=C.border }}}
                  >
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', gap:8, marginBottom:6 }}>
                      <div style={{ display:'flex', gap:8, alignItems:'flex-start', flex:1 }}>
                        <span style={{ fontSize:12, flexShrink:0, marginTop:2 }}>{SEV_ICON[sev]||'⚪'}</span>
                        <span style={{ fontSize:14, color:C.text, fontWeight:700, lineHeight:1.4 }}>{inc.message||'Anomaly detected'}</span>
                      </div>
                      <Chip label={sev} color={sevColor}/>
                    </div>
                    <div style={{ display:'flex', gap:12, fontSize:10, fontFamily:'JetBrains Mono,monospace', color:C.muted, paddingLeft:20 }}>
                      <span>{inc.timestamp?.slice(0,19).replace('T',' ')}</span>
                      {inc.score > 0 && <span style={{ color:C.purple }}>AI {inc.score?.toFixed(3)}</span>}
                      {inc.rapport && <span style={{ color:C.yellow }}>📄 report</span>}
                    </div>
                  </div>
                )
              })}
            </div>

            {selected && (
              <div style={{ display:'flex', flexDirection:'column', gap:12, position:'sticky', top:16 }}>
                {!reportOpen && (
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
                    {incidentText ? (
                      <div style={{ borderTop:`1px solid ${C.border}`, paddingTop:14 }}>
                        <MixedContent text={incidentText}/>
                      </div>
                    ) : (
                      <div style={{ fontSize:13, color:C.sub, lineHeight:1.7, borderTop:`1px solid ${C.border}`, paddingTop:14 }}>
                        {selected.message}
                      </div>
                    )}
                    {recoText && (
                      <div style={{ marginTop:14, background:'#0a1a0e', border:`1px solid ${C.green}25`, borderRadius:8, padding:'12px 14px' }}>
                        <div style={{ fontSize:11, fontWeight:700, color:C.green, letterSpacing:'0.1em', marginBottom:8, fontFamily:'JetBrains Mono,monospace' }}>
                          RECOMMENDED ACTION
                        </div>
                        <MixedContent text={recoText}/>
                      </div>
                    )}
                  </Card>
                )}

                {selected.rapport && (
                  <Card style={{ padding:'14px 18px' }}>
                    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
                      <div>
                        <div style={{ fontSize:12, fontWeight:600, color:C.yellow, marginBottom:3 }}>📄 Full Incident Report</div>
                        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace' }}>{selected.rapport}</div>
                      </div>
                      <button onClick={()=>reportOpen?closeReport():openReport(selected.rapport)}
                        style={{ padding:'7px 16px', borderRadius:7, fontSize:12, fontWeight:600, fontFamily:'JetBrains Mono,monospace', cursor:'pointer', border:`1px solid ${C.yellow}60`, background:reportOpen?C.yellow+'20':C.bg, color:C.yellow, transition:'all 0.15s', flexShrink:0 }}>
                        {loadingReport ? '⟳' : reportOpen ? '✕ Close' : '↓ Open report'}
                      </button>
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