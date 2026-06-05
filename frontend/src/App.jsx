/**
 * OpsPilot Enterprise — Infrastructure AI Platform
 * Design foncé original + corrections :
 *   1. Rendu Markdown correct des blocs bash (header + bouton copier)
 *   2. Page d'accueil assistant : logo + boutons rapides seulement (pas de texte descriptif)
 *   3. Édition de messages (bouton crayon au hover)
 */

import React, { useState, useEffect, useRef, useCallback } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts'

function useWebSocket(url) {
  const [connected, setConnected] = useState(false)
  const wsRef      = useRef(null)
  const retryRef   = useRef(null)
  const handlerRef = useRef(null)
  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(url)
    wsRef.current = ws
    ws.onopen    = () => setConnected(true)
    ws.onclose   = () => { setConnected(false); retryRef.current = setTimeout(connect, 3000) }
    ws.onerror   = () => ws.close()
    ws.onmessage = (e) => { try { handlerRef.current?.(JSON.parse(e.data)) } catch {} }
  }, [url])
  useEffect(() => { connect(); return () => { clearTimeout(retryRef.current); wsRef.current?.close() } }, [connect])
  const send = useCallback((data) => { if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(data)) }, [])
  return { connected, send, handlerRef }
}

const C = {
  bg: '#04070f', surface: '#080e1a', card: '#0b1220', border: '#111d2e', borderHi: '#1a2d44',
  text: '#e2e8f0', sub: '#64748b', muted: '#2d3d52',
  blue: '#3b82f6', green: '#22c55e', yellow: '#eab308', orange: '#f97316',
  red: '#ef4444', purple: '#a855f7', cyan: '#06b6d4',
}
const riskColor     = (v) => v > 90 ? C.red : v > 75 ? C.orange : v > 50 ? C.yellow : C.green
const severityColor = (s = '') => ({ CRITICAL: C.red, CRITIQUE: C.red, HIGH: C.orange, IMPORTANT: C.orange, MEDIUM: C.yellow, SURVEILLANCE: C.yellow, MONITORING: C.yellow, LOW: C.green, NORMAL: C.green })[s.toUpperCase()] || C.sub
const normalizeSeverity = (s = '') => ({'CRITIQUE':'CRITICAL','IMPORTANT':'HIGH','SURVEILLANCE':'MONITORING','CRITICAL':'CRITICAL','HIGH':'HIGH','MONITORING':'MONITORING','NORMAL':'NORMAL'})[s.toUpperCase()] || s
const fmtUptime = (h) => { if (!h) return '—'; const d = Math.floor(h/24), r = Math.floor(h%24); return d > 0 ? `${d}d ${r}h` : `${r}h` }
const fmtTime   = (s) => `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor((s%3600)/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`

function Card({ children, style = {}, glow }) {
  return <div style={{ background: C.card, borderRadius: 10, border: `1px solid ${glow ? glow+'50' : C.border}`, boxShadow: glow ? `0 0 20px ${glow}08` : 'none', ...style }}>{children}</div>
}
function SectionLabel({ children, color = C.sub }) {
  return <div style={{ fontSize: 10, fontWeight: 700, letterSpacing: '0.14em', color, textTransform: 'uppercase', fontFamily: 'JetBrains Mono, monospace', marginBottom: 14 }}>{children}</div>
}
function Chip({ label, color }) {
  const c = color || C.sub
  return <span style={{ padding: '2px 10px', borderRadius: 20, fontSize: 10, fontWeight: 700, letterSpacing: '0.08em', fontFamily: 'JetBrains Mono, monospace', background: c+'18', border: `1px solid ${c}45`, color: c, whiteSpace: 'nowrap' }}>{label}</span>
}
function MetricRow({ label, value = 0, used, total }) {
  const c = riskColor(value)
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: C.sub }}>{label}</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {used != null && total != null && <span style={{ color: C.muted, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>{used} / {total} GB</span>}
          <span style={{ color: c, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace' }}>{value?.toFixed(1)}%</span>
        </div>
      </div>
      <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
        <div style={{ height: '100%', width: `${Math.min(100, value||0)}%`, background: c, borderRadius: 2, transition: 'width 0.7s ease' }}/>
      </div>
    </div>
  )
}
function OnlineDot({ online, pulse = true }) {
  return <span style={{ width: 7, height: 7, borderRadius: '50%', display: 'inline-block', background: online ? C.green : C.red, flexShrink: 0, animation: online && pulse ? 'pulse 2s infinite' : 'none' }}/>
}

// ── CORRECTION 1 : Markdown avec rendu propre des blocs bash ─────────────
function MD({ text = '', small }) {
  const fs = small ? 12 : 13

  const segments = []
  const re = /```(\w*)\n?([\s\S]*?)```/g
  let last = 0, m
  while ((m = re.exec(text)) !== null) {
    if (m.index > last) segments.push({ type: 'text', content: text.slice(last, m.index) })
    segments.push({ type: 'code', lang: m[1] || 'bash', content: m[2].trim() })
    last = m.index + m[0].length
  }
  if (last < text.length) segments.push({ type: 'text', content: text.slice(last) })

  const renderText = (t) => {
    // Nettoyer les lignes redondantes dans les recommandations
    t = t.replace(/^\*\*Fix title:\*\*[^\n]*\n?/m, '')
    const html = t
      .replace(/`([^`]+)`/g, `<code style="background:#0f172a;padding:2px 6px;border-radius:4px;font-family:JetBrains Mono,monospace;font-size:11px;color:#a78bfa">$1</code>`)
      .replace(/\*\*([^*]+)\*\*/g, `<strong style="color:#f1f5f9;font-weight:600">$1</strong>`)
      .replace(/^### (.+)$/gm, `<div style="font-size:11px;font-weight:700;color:#64748b;margin:14px 0 6px;text-transform:uppercase;letter-spacing:0.07em">$1</div>`)
      .replace(/^## (.+)$/gm, `<div style="font-size:14px;font-weight:700;color:#94a3b8;margin:14px 0 8px">$1</div>`)
      .replace(/^# (.+)$/gm, `<div style="font-size:16px;font-weight:800;color:#e2e8f0;margin:14px 0 8px">$1</div>`)
      .replace(/^\d+\. (.+)$/gm, `<div style="display:flex;gap:8px;margin:5px 0;color:#94a3b8"><span style="color:#3b82f6;font-family:JetBrains Mono,monospace;font-size:11px;flex-shrink:0">•</span><span>$1</span></div>`)
      .replace(/^- (.+)$/gm, `<div style="display:flex;gap:8px;margin:4px 0;color:#94a3b8"><span style="color:#334155;margin-top:4px;flex-shrink:0">▸</span><span>$1</span></div>`)
      .replace(/\n\n/g, '<br/><br/>').replace(/\n/g, '<br/>')
    return <div dangerouslySetInnerHTML={{ __html: html }} style={{ lineHeight: 1.8, color: '#94a3b8', fontSize: fs }}/>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {segments.map((seg, i) =>
        seg.type === 'code' ? (
          <div key={i} style={{ margin: '10px 0', borderRadius: 8, overflow: 'hidden', border: `1px solid ${C.border}` }}>
            <div style={{ background: '#060c16', padding: '6px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${C.border}` }}>
              <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: C.muted }}>{seg.lang}</span>
              <button
                onClick={() => navigator.clipboard?.writeText(seg.content)}
                style={{ background: 'transparent', border: `1px solid ${C.border}`, color: C.muted, borderRadius: 4, padding: '2px 8px', fontSize: 10, cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace' }}
                onMouseEnter={e => { e.currentTarget.style.borderColor = C.blue; e.currentTarget.style.color = C.blue }}
                onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.muted }}
              >copier</button>
            </div>
            <pre style={{ background: '#030812', margin: 0, padding: '14px 16px', overflowX: 'auto', fontSize: 12, lineHeight: 1.7, fontFamily: 'JetBrains Mono, monospace', color: '#7dd3fc', whiteSpace: 'pre' }}>{seg.content}</pre>
          </div>
        ) : (
          <div key={i}>{renderText(seg.content)}</div>
        )
      )}
    </div>
  )
}

const ChartTip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: C.card, border: `1px solid ${C.borderHi}`, borderRadius: 6, padding: '8px 12px', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
      {payload.map((p, i) => <div key={i} style={{ color: p.color, display: 'flex', gap: 12, justifyContent: 'space-between' }}><span>{p.name}</span><span style={{ fontWeight: 700 }}>{p.value?.toFixed(1)}%</span></div>)}
    </div>
  )
}

function PageDashboard({ cluster, history, incidents, agentLog, dernier_lstm = { score:0, score_if:0, score_lstm:0, lstm_ready:false, seuil:0.5, drift:false } }) {
  if (!cluster) return <Placeholder icon="◈" text="Connexion au cluster en cours..."/>
  const { noeuds = [], vms = [], alertes = [] } = cluster
  const avgCPU = noeuds.length ? noeuds.reduce((s,n) => s+n.cpu_pct, 0)/noeuds.length : 0
  const avgRAM = noeuds.length ? noeuds.reduce((s,n) => s+n.ram_pct, 0)/noeuds.length : 0
  const health = alertes.some(a => a.niveau==='CRITIQUE') ? 'CRITICAL' : alertes.length > 0 ? 'DEGRADED' : 'OPERATIONAL'
  const healthColor = { OPERATIONAL: C.green, DEGRADED: C.yellow, CRITICAL: C.red }[health]
  const chartData = history.slice(-50).map((h,i) => ({ i, CPU: h.cpu??0, Memory: h.ram??0 }))
  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      <div style={{ background: healthColor+'0d', border: `1px solid ${healthColor}30`, borderRadius: 10, padding: '14px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <OnlineDot online={health==='OPERATIONAL'}/>
          <span style={{ fontWeight: 700, color: healthColor, fontSize: 14 }}>System Status: {health}</span>
          <span style={{ color: C.sub, fontSize: 12 }}>Last check: {new Date().toLocaleTimeString('fr-FR')}</span>
        </div>
        <div style={{ display: 'flex', gap: 20, fontSize: 12, color: C.sub, fontFamily: 'JetBrains Mono, monospace' }}>
          <span>{noeuds.length} Serveurs</span>
          <span style={{ color: C.green }}>{cluster.vms_running??0} Active VMs</span>
          {alertes.length > 0 && <span style={{ color: C.orange }}>{alertes.length} Alert(s)</span>}
        </div>
      </div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4,1fr)', gap: 12 }}>
        {[
          { label: 'Avg CPU Load',     value: avgCPU.toFixed(1)+'%', color: riskColor(avgCPU),        sub: `${noeuds.length} servers` },
          { label: 'Avg Memory Usage', value: avgRAM.toFixed(1)+'%', color: riskColor(avgRAM),        sub: 'across cluster' },
          { label: 'Active VMs',      value: cluster.vms_running??0, color: C.green,                  sub: `${vms.length} total` },
          { label: 'Open Incidents',   value: incidents.length,        color: incidents.length?C.red:C.green, sub: `${alertes.filter(a=>a.niveau==='CRITIQUE').length} critical` },
        ].map(({ label, value, color, sub }) => (
          <Card key={label} glow={color} style={{ padding: '16px 18px' }}>
            <div style={{ fontSize: 10, color: C.muted, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.1em', marginBottom: 8 }}>{label.toUpperCase()}</div>
            <div style={{ fontSize: 28, fontWeight: 800, color, fontFamily: 'JetBrains Mono, monospace', marginBottom: 4 }}>{value}</div>
            <div style={{ fontSize: 11, color: C.sub }}>{sub}</div>
          </Card>
        ))}
      </div>
      {/* Panneau scores IA — visible seulement si un score existe */}
      {dernier_lstm && (dernier_lstm.score > 0 || dernier_lstm.score_if > 0) && (
        <div style={{ display:'flex', gap:12 }}>
          {/* Isolation Forest */}
          <Card style={{ flex:1, padding:'12px 16px', border:`1px solid ${(dernier_lstm.score_if||0)>0.5?'#ef444450':'#1e2d4a'}` }}>
            <div style={{ fontSize:9, color:'#3d5280', fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em', marginBottom:6 }}>ISOLATION FOREST — IMMEDIATE DETECTION</div>
            <div style={{ display:'flex', alignItems:'baseline', gap:8, marginBottom:8 }}>
              <span style={{ fontSize:24, fontWeight:800, color:(dernier_lstm.score_if||0)>0.8?'#ef4444':(dernier_lstm.score_if||0)>0.5?'#f97316':'#22c55e', fontFamily:'JetBrains Mono,monospace' }}>
                {((dernier_lstm.score_if||0)*100).toFixed(0)}%
              </span>
              <span style={{ fontSize:11, color:'#64748b' }}>anomaly score</span>
            </div>
            <div style={{ height:3, background:'#1e2d4a', borderRadius:2 }}>
              <div style={{ height:'100%', width:`${(dernier_lstm.score_if||0)*100}%`, borderRadius:2, background:(dernier_lstm.score_if||0)>0.8?'#ef4444':(dernier_lstm.score_if||0)>0.5?'#f97316':'#22c55e', transition:'width 1s ease' }}/>
            </div>
            <div style={{ fontSize:10, color:'#3d5280', marginTop:6 }}>✓ Active since startup</div>
          </Card>

          {/* LSTM */}
          <Card style={{ flex:1, padding:'12px 16px', border:`1px solid ${(dernier_lstm.score_lstm||0)>0.5?'#ef444450':'#1e2d4a'}` }}>
            <div style={{ fontSize:9, color:'#3d5280', fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em', marginBottom:6 }}>LSTM AUTOENCODER — TEMPORAL LEARNING</div>
            <div style={{ display:'flex', alignItems:'baseline', gap:8, marginBottom:8 }}>
              <span style={{ fontSize:24, fontWeight:800, color:dernier_lstm.lstm_ready?((dernier_lstm.score_lstm||0)>0.8?'#ef4444':(dernier_lstm.score_lstm||0)>0.5?'#f97316':'#22c55e'):'#3d5280', fontFamily:'JetBrains Mono,monospace' }}>
                {dernier_lstm.lstm_ready ? `${((dernier_lstm.score_lstm||0)*100).toFixed(0)}%` : '—'}
              </span>
              <span style={{ fontSize:11, color:'#64748b' }}>{dernier_lstm.lstm_ready ? 'anomaly score' : 'learning...'}</span>
            </div>
            <div style={{ height:3, background:'#1e2d4a', borderRadius:2 }}>
              <div style={{ height:'100%', width:dernier_lstm.lstm_ready?`${(dernier_lstm.score_lstm||0)*100}%`:'30%', borderRadius:2, background:dernier_lstm.lstm_ready?((dernier_lstm.score_lstm||0)>0.5?'#f97316':'#22c55e'):'#1e2d4a', transition:'width 1s ease', animation:!dernier_lstm.lstm_ready?'pulse 2s infinite':undefined }}/>
            </div>
            <div style={{ fontSize:10, color:'#3d5280', marginTop:6 }}>
              {dernier_lstm.lstm_ready ? '✓ Trained on real data' : '⟳ Collecting data to train...'}
            </div>
          </Card>

          {/* Score hybride combiné */}
          <Card glow={(dernier_lstm.score||0)>0.5?'#ef4444':undefined} style={{ flex:1, padding:'12px 16px' }}>
            <div style={{ fontSize:9, color:'#3d5280', fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em', marginBottom:6 }}>HYBRID SCORE — FINAL DECISION</div>
            <div style={{ display:'flex', alignItems:'baseline', gap:8, marginBottom:8 }}>
              <span style={{ fontSize:24, fontWeight:800, fontFamily:'JetBrains Mono,monospace', color:(dernier_lstm.score||0)>0.8?'#ef4444':(dernier_lstm.score||0)>0.5?'#f97316':'#22c55e' }}>
                {((dernier_lstm.score||0)*100).toFixed(0)}%
              </span>
              <span style={{ fontSize:11, color:'#64748b' }}>
                {(dernier_lstm.score||0)>0.8?'CRITICAL':(dernier_lstm.score||0)>0.5?'SUSPICIOUS':'NORMAL'}
              </span>
            </div>
            <div style={{ height:3, background:'#1e2d4a', borderRadius:2 }}>
              <div style={{ height:'100%', width:`${(dernier_lstm.score||0)*100}%`, borderRadius:2, background:(dernier_lstm.score||0)>0.8?'#ef4444':(dernier_lstm.score||0)>0.5?'#f97316':'#22c55e', transition:'width 1s ease' }}/>
            </div>
            <div style={{ fontSize:10, color:'#3d5280', marginTop:6 }}>
              {dernier_lstm.lstm_ready ? 'IF 40% + LSTM 60%' : 'IF 100% (LSTM learning)'}
            </div>
          </Card>
        </div>
      )}

      <Card style={{ padding: '16px 18px' }}>
        <SectionLabel>Performance Overview — CPU & Memory (last 50 samples)</SectionLabel>
        <ResponsiveContainer width="100%" height={160}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
            <defs>
              <linearGradient id="gCPU" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.blue} stopOpacity={0.3}/><stop offset="95%" stopColor={C.blue} stopOpacity={0}/></linearGradient>
              <linearGradient id="gMEM" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.purple} stopOpacity={0.25}/><stop offset="95%" stopColor={C.purple} stopOpacity={0}/></linearGradient>
            </defs>
            <CartesianGrid stroke={C.border} strokeDasharray="3 3"/>
            <XAxis dataKey="i" tick={false} axisLine={false}/>
            <YAxis domain={[0,100]} tick={{ fill: C.muted, fontSize: 10 }} axisLine={false} tickLine={false}/>
            <Tooltip content={<ChartTip/>}/>
            <ReferenceLine y={80} stroke={C.orange} strokeDasharray="3 3" opacity={0.4}/>
            <Area type="monotone" dataKey="CPU"    stroke={C.blue}   fill="url(#gCPU)" strokeWidth={1.5} dot={false} isAnimationActive={false}/>
            <Area type="monotone" dataKey="Memory" stroke={C.purple} fill="url(#gMEM)" strokeWidth={1.5} dot={false} isAnimationActive={false}/>
          </AreaChart>
        </ResponsiveContainer>
      </Card>
      <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 12 }}>
        <Card style={{ padding: '16px 18px' }}>
          <SectionLabel>Server Health</SectionLabel>
          {noeuds.map((n,i) => (
            <div key={i} style={{ marginBottom: 16, paddingBottom: 16, borderBottom: i<noeuds.length-1 ? `1px solid ${C.border}` : 'none' }}>
              <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                <div>
                  <div style={{ fontSize: 13, fontWeight: 700, color: C.text }}>{n.nom}</div>
                  <div style={{ fontSize: 11, color: C.sub, fontFamily: 'JetBrains Mono, monospace' }}>Uptime {fmtUptime(n.uptime_h)} · {n.ram_total_gb} GB RAM</div>
                </div>
                <div style={{ display: 'flex', alignItems: 'center', gap: 6, fontSize: 11, fontFamily: 'JetBrains Mono, monospace', color: n.statut==='online'?C.green:C.red }}>
                  <OnlineDot online={n.statut==='online'}/>{n.statut==='online'?'Online':'Offline'}
                </div>
              </div>
              <MetricRow label="CPU Utilization"    value={n.cpu_pct}/>
              <MetricRow label="Memory Utilization" value={n.ram_pct}  used={n.ram_used_gb}  total={n.ram_total_gb}/>
              <MetricRow label="Storage"            value={n.disk_pct} used={n.disk_used_gb} total={n.disk_total_gb}/>
            </div>
          ))}
        </Card>
        <Card style={{ padding: '16px 18px' }}>
          <SectionLabel>Recent Activity</SectionLabel>
          <div style={{ display: 'flex', flexDirection: 'column', maxHeight: 300, overflowY: 'auto' }}>
            {agentLog.slice(-12).reverse().map((l,i) => (
              <div key={i} style={{ display: 'flex', gap: 10, padding: '6px 0', borderBottom: `1px solid ${C.border}`, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
                <span style={{ color: C.muted, flexShrink: 0, width: 55 }}>{l.time}</span>
                <span style={{ color: l.color||C.sub, flexShrink: 0, width: 90, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.label}</span>
                <span style={{ color: C.sub, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{l.msg}</span>
              </div>
            ))}
            {agentLog.length===0 && <div style={{ color: C.muted, fontSize: 12, fontStyle: 'italic' }}>Waiting for activity...</div>}
          </div>
        </Card>
      </div>
      {alertes.length > 0 && (
        <Card style={{ padding: '16px 18px' }} glow={C.red}>
          <SectionLabel color={C.red}>Active Alerts</SectionLabel>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {alertes.map((a,i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: C.bg, borderRadius: 8, padding: '10px 14px', border: `1px solid ${severityColor(a.niveau)}25` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: severityColor(a.niveau), flexShrink: 0 }}/>
                  <span style={{ fontSize: 13, color: C.text }}>{a.message}</span>
                </div>
                <Chip label={normalizeSeverity(a.niveau)} color={severityColor(a.niveau)}/>
              </div>
            ))}
          </div>
        </Card>
      )}
    </div>
  )
}

function PageInfrastructure({ cluster, history }) {
  const [selected, setSelected] = useState(null)
  if (!cluster) return <Placeholder icon="◉" text="Loading infrastructure data..."/>
  const { noeuds=[], vms=[] } = cluster
  const all = [...noeuds.map(n=>({...n,_type:'server',_id:n.nom})), ...vms.map(v=>({...v,_type:'vm',_id:`vm-${v.vmid}`}))]
  const sel = all.find(r=>r._id===selected)
  const chartData = history.slice(-40).map((h,i)=>({i, CPU:h.cpu??0, Memory:h.ram??0}))
  return (
    <div style={{ display: 'grid', gridTemplateColumns: '300px 1fr', gap: 16, minHeight: '70vh' }}>
      <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
        <SectionLabel>Infrastructure Resources</SectionLabel>
        <div style={{ fontSize: 10, color: C.muted, fontFamily: 'JetBrains Mono, monospace', padding: '4px 2px', letterSpacing: '0.08em' }}>PHYSICAL SERVERS ({noeuds.length})</div>
        {noeuds.map((n,i)=>(
          <div key={i} onClick={()=>setSelected(n.nom)} style={{ background: selected===n.nom?C.surface:C.card, border:`1px solid ${selected===n.nom?C.blue+'60':C.border}`, borderRadius:8, padding:'12px 14px', cursor:'pointer', transition:'all 0.15s' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
              <div><div style={{ fontSize:13, fontWeight:600, color:C.text }}>{n.nom}</div><div style={{ fontSize:10, color:C.sub, fontFamily:'JetBrains Mono, monospace' }}>Proxmox Hypervisor</div></div>
              <div style={{ display:'flex', alignItems:'center', gap:5, fontSize:11, color:n.statut==='online'?C.green:C.red, fontFamily:'JetBrains Mono, monospace' }}><OnlineDot online={n.statut==='online'}/>{n.statut==='online'?'Online':'Offline'}</div>
            </div>
            <div style={{ display:'flex', gap:14, fontSize:11, fontFamily:'JetBrains Mono, monospace' }}>
              <span style={{ color:riskColor(n.cpu_pct) }}>CPU {n.cpu_pct?.toFixed(0)}%</span>
              <span style={{ color:riskColor(n.ram_pct) }}>MEM {n.ram_pct?.toFixed(0)}%</span>
              <span style={{ color:riskColor(n.disk_pct) }}>DSK {n.disk_pct?.toFixed(0)}%</span>
            </div>
          </div>
        ))}
        <div style={{ fontSize:10, color:C.muted, fontFamily:'JetBrains Mono, monospace', padding:'8px 2px 4px', letterSpacing:'0.08em' }}>VIRTUAL MACHINES ({vms.length})</div>
        {vms.map((v,i)=>(
          <div key={i} onClick={()=>setSelected(`vm-${v.vmid}`)} style={{ background:selected===`vm-${v.vmid}`?C.surface:C.card, border:`1px solid ${selected===`vm-${v.vmid}`?C.blue+'60':v.statut==='running'?C.green+'20':C.border}`, borderRadius:8, padding:'12px 14px', cursor:'pointer', transition:'all 0.15s' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:6 }}>
              <div><div style={{ fontSize:13, fontWeight:600, color:C.text }}>{v.nom}</div><div style={{ fontSize:10, color:C.sub, fontFamily:'JetBrains Mono, monospace' }}>VM {v.vmid} · {v.noeud}</div></div>
              <div style={{ display:'flex', alignItems:'center', gap:5, fontSize:11, color:v.statut==='running'?C.green:C.sub, fontFamily:'JetBrains Mono, monospace' }}><OnlineDot online={v.statut==='running'}/>{v.statut==='running'?'Running':'Stopped'}</div>
            </div>
            {v.statut==='running' && <div style={{ display:'flex', gap:14, fontSize:11, fontFamily:'JetBrains Mono, monospace' }}><span style={{ color:riskColor(v.cpu_pct) }}>CPU {v.cpu_pct?.toFixed(0)}%</span><span style={{ color:riskColor(v.ram_pct) }}>MEM {v.ram_pct?.toFixed(0)}%</span><span style={{ color:C.sub }}>↑ {fmtUptime(v.uptime_h)}</span></div>}
          </div>
        ))}
      </div>
      {!sel ? (
        <div style={{ display:'flex', alignItems:'center', justifyContent:'center' }}>
          <div style={{ textAlign:'center', color:C.muted }}><div style={{ fontSize:36, marginBottom:12 }}>◉</div><div style={{ fontSize:14 }}>Select a resource to view details</div></div>
        </div>
      ) : (
        <div style={{ display:'flex', flexDirection:'column', gap:14 }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
            <div><h2 style={{ fontSize:20, fontWeight:800, color:C.text, marginBottom:4 }}>{sel.nom}</h2><div style={{ fontSize:12, color:C.sub, fontFamily:'JetBrains Mono, monospace' }}>{sel._type==='server'?`Physical Hypervisor · Proxmox VE · ${sel.ram_total_gb} GB RAM`:`Virtual Machine · ID ${sel.vmid} · Hosted on ${sel.noeud}`}</div></div>
            <div style={{ display:'flex', alignItems:'center', gap:6, fontSize:12, color:sel.statut==='online'||sel.statut==='running'?C.green:C.red, fontFamily:'JetBrains Mono, monospace' }}><OnlineDot online={sel.statut==='online'||sel.statut==='running'}/>{sel.statut==='online'?'Online':sel.statut==='running'?'Running':'Offline'}</div>
          </div>
          <div style={{ display:'grid', gridTemplateColumns:'repeat(3,1fr)', gap:12 }}>
            {[
              { label:'CPU Utilization',    value:sel.cpu_pct,     color:riskColor(sel.cpu_pct),     sub:sel.cpus?`${sel.cpus} vCPUs`:'Physical cores' },
              { label:'Memory Utilization', value:sel.ram_pct,     color:riskColor(sel.ram_pct),     sub:`${sel.ram_used_gb??0}/${sel.ram_total_gb??0} GB` },
              { label:'Stockage',            value:sel.disk_pct??0, color:riskColor(sel.disk_pct??0), sub:sel.disk_used_gb?`${sel.disk_used_gb}/${sel.disk_total_gb} GB`:`${sel.disk_gb??0} GB` },
            ].map(({ label, value, color, sub })=>(
              <Card key={label} glow={color} style={{ padding:'14px 16px' }}>
                <div style={{ fontSize:9, color:C.muted, fontFamily:'JetBrains Mono, monospace', letterSpacing:'0.1em', marginBottom:8 }}>{label.toUpperCase()}</div>
                <div style={{ fontSize:26, fontWeight:800, color, fontFamily:'JetBrains Mono, monospace', marginBottom:4 }}>{value?.toFixed(1)}%</div>
                <div style={{ fontSize:11, color:C.sub, marginBottom:10 }}>{sub}</div>
                <div style={{ height:3, background:C.border, borderRadius:2 }}><div style={{ height:'100%', width:`${Math.min(100,value||0)}%`, background:color, borderRadius:2 }}/></div>
              </Card>
            ))}
          </div>
          <Card style={{ padding:'16px 18px' }}>
            <SectionLabel>Performance History</SectionLabel>
            <ResponsiveContainer width="100%" height={160}>
              <AreaChart data={chartData} margin={{ top:4, right:4, bottom:0, left:-24 }}>
                <defs>
                  <linearGradient id="gC" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.blue} stopOpacity={0.3}/><stop offset="95%" stopColor={C.blue} stopOpacity={0}/></linearGradient>
                  <linearGradient id="gM" x1="0" y1="0" x2="0" y2="1"><stop offset="5%" stopColor={C.purple} stopOpacity={0.25}/><stop offset="95%" stopColor={C.purple} stopOpacity={0}/></linearGradient>
                </defs>
                <CartesianGrid stroke={C.border} strokeDasharray="3 3"/>
                <XAxis dataKey="i" tick={false} axisLine={false}/>
                <YAxis domain={[0,100]} tick={{ fill:C.muted, fontSize:10 }} axisLine={false} tickLine={false}/>
                <Tooltip content={<ChartTip/>}/>
                <ReferenceLine y={80} stroke={C.orange} strokeDasharray="3 3" opacity={0.5}/>
                <Area type="monotone" dataKey="CPU"    stroke={C.blue}   fill="url(#gC)" strokeWidth={1.5} dot={false} isAnimationActive={false}/>
                <Area type="monotone" dataKey="Memory" stroke={C.purple} fill="url(#gM)" strokeWidth={1.5} dot={false} isAnimationActive={false}/>
              </AreaChart>
            </ResponsiveContainer>
          </Card>
        </div>
      )}
    </div>
  )
}

function PageIncidents({ incidents }) {
  const [sel, setSel]           = useState(null)
  const [rapportContent, setRapportContent] = useState(null)
  const [loadingRapport, setLoadingRapport] = useState(false)

  const selected = sel !== null ? [...incidents].reverse()[sel] : null

  const openRapport = async (nom) => {
    if (!nom) return
    setLoadingRapport(true)
    try {
      const r = await fetch(`/api/rapports/${nom}`)
      const d = await r.json()
      setRapportContent(d.contenu || 'No content available')
    } catch {
      setRapportContent('Error loading report')
    } finally {
      setLoadingRapport(false)
    }
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div>
          <h2 style={{ fontSize:18, fontWeight:700, color:C.text, marginBottom:4 }}>Incident Management</h2>
          <div style={{ fontSize:12, color:C.sub }}>Automatically detected by the AI monitoring engine</div>
        </div>
        <Chip label={`${incidents.length} INCIDENT${incidents.length > 1 ? 'S' : ''}`} color={incidents.length>0?C.red:C.green}/>
      </div>

      {incidents.length===0 ? (
        <Card style={{ padding:'50px 0', textAlign:'center' }}>
          <div style={{ fontSize:36, marginBottom:14 }}>✓</div>
          <div style={{ fontSize:14, color:C.green, fontWeight:700 }}>All Systems Operational</div>
        </Card>
      ) : (
        <div style={{ display:'grid', gridTemplateColumns:selected?'1fr 1.2fr':'1fr', gap:16 }}>

          {/* Liste des incidents */}
          <div style={{ display:'flex', flexDirection:'column', gap:8 }}>
            {[...incidents].reverse().map((inc,i)=>(
              <div key={i}
                onClick={()=>{ setSel(i===sel?null:i); setRapportContent(null) }}
                style={{ background:sel===i?C.surface:C.card, border:`1px solid ${sel===i?severityColor(inc.statut)+'60':C.border}`, borderRadius:10, padding:'14px 16px', cursor:'pointer', transition:'all 0.15s' }}
              >
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:8 }}>
                  <div style={{ fontSize:13, color:C.text, fontWeight:600, flex:1, marginRight:12 }}>
                    {inc.message||'Anomaly detected'}
                  </div>
                  <Chip label={normalizeSeverity(inc.statut)||'INCIDENT'} color={severityColor(inc.statut)}/>
                </div>
                <div style={{ display:'flex', gap:16, fontSize:11, fontFamily:'JetBrains Mono, monospace', color:C.sub }}>
                  <span>{inc.timestamp?.slice(0,19).replace('T',' ')}</span>
                  {inc.score!=null && inc.score>0 && (
                    <span>Score <span style={{ color:C.purple }}>{inc.score?.toFixed(3)}</span></span>
                  )}
                  {inc.rapport && (
                    <span style={{ color:C.yellow }}>📄 report available</span>
                  )}
                </div>
              </div>
            ))}
          </div>

          {/* Panneau de détail */}
          {selected && (
            <div style={{ display:'flex', flexDirection:'column', gap:12 }}>

              {/* En-tête incident */}
              <Card style={{ padding:'16px 18px' }}>
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
                  <div style={{ fontSize:14, fontWeight:700, color:C.text }}>Incident Details</div>
                  <Chip label={normalizeSeverity(selected.statut)||'INCIDENT'} color={severityColor(selected.statut)}/>
                </div>
                <div style={{ fontSize:12, color:C.muted, fontFamily:'JetBrains Mono, monospace', marginBottom:10 }}>
                  {selected.timestamp?.slice(0,19).replace('T',' ')}
                </div>

                {/* Analyse LLM de l incident (section ---INCIDENT---) */}
                {selected.incident && (
                  <div style={{ marginTop:8 }}>
                    <MD text={selected.incident}/>
                  </div>
                )}

                {/* Si pas d analyse LLM, afficher juste le message */}
                {!selected.incident && (
                  <div style={{ fontSize:13, color:C.sub, lineHeight:1.7 }}>
                    {selected.message}
                  </div>
                )}
              </Card>

              {/* Bouton ouvrir le rapport complet */}
              {selected.rapport && (
                <Card style={{ padding:'14px 18px' }}>
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom: rapportContent ? 12 : 0 }}>
                    <div>
                      <div style={{ fontSize:12, fontWeight:600, color:C.yellow, marginBottom:4 }}>📄 Full Report</div>
                      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono, monospace' }}>{selected.rapport}</div>
                    </div>
                    <button
                      onClick={() => rapportContent ? setRapportContent(null) : openRapport(selected.rapport)}
                      style={{ padding:'6px 14px', borderRadius:7, border:`1px solid ${C.yellow}50`, background:C.bg, color:C.yellow, cursor:'pointer', fontSize:12, fontFamily:'JetBrains Mono, monospace', transition:'all 0.15s', flexShrink:0 }}
                      onMouseEnter={e=>{ e.currentTarget.style.background=C.yellow+'15' }}
                      onMouseLeave={e=>{ e.currentTarget.style.background=C.bg }}
                    >
                      {loadingRapport ? '⟳ Loading...' : rapportContent ? '✕ Close' : '↓ Open report'}
                    </button>
                  </div>

                  {/* Contenu du rapport Markdown */}
                  {rapportContent && (
                    <div style={{ marginTop:12, maxHeight:400, overflowY:'auto', borderTop:`1px solid ${C.border}`, paddingTop:12 }}>
                      <pre style={{ fontFamily:'JetBrains Mono, monospace', fontSize:11, color:C.sub, whiteSpace:'pre-wrap', lineHeight:1.8 }}>
                        {rapportContent}
                      </pre>
                    </div>
                  )}
                </Card>
              )}
            </div>
          )}
        </div>
      )}
    </div>
  )
}

function PageMonitoringRules({ reglesDynamiques = [] }) {
  const [regenerating, setRegenerating] = React.useState(false)
  const [regenerated,  setRegenerated]  = React.useState(false)

  // Règles de fallback — doc Proxmox officielle
  const reglesFallback = [
    { metric: 'server.cpu_pct',  operateur: '>',  seuil: 80,  duree_min: 5,  severite: 'CRITICAL',    description: "Sustained CPU > 80% causes contention between VMs and latency spikes.", source: 'Proxmox VE Docs', action: "pvesh get /nodes/{node}/status | grep cpu" },
    { metric: 'server.cpu_pct',  operateur: '>',  seuil: 65,  duree_min: 15, severite: 'HIGH',        description: "Early CPU warning — plan VM migration before reaching critical threshold.", source: 'Capacity Planning', action: "pvesh get /nodes/{node}/status" },
    { metric: 'server.ram_pct',  operateur: '>',  seuil: 85,  duree_min: 5,  severite: 'CRITICAL',    description: "RAM > 85% triggers swap and risks OOM kills on hypervisor.", source: 'Linux/Proxmox Best Practices', action: "pvesh get /nodes/{node}/status | grep mem" },
    { metric: 'server.ram_pct',  operateur: '>',  seuil: 75,  duree_min: 15, severite: 'HIGH',        description: "Memory pressure building — review VM allocations before saturation.", source: 'Proxmox Memory Management', action: "pvesh get /nodes/{node}/status" },
    { metric: 'server.disk_pct', operateur: '>',  seuil: 90,  duree_min: 0,  severite: 'CRITICAL',    description: "Disk > 90% on LVM-thin Proxmox: writes fail, VMs may corrupt.", source: 'Proxmox Storage Docs', action: "pvesm status && df -h" },
    { metric: 'server.disk_pct', operateur: '>',  seuil: 80,  duree_min: 0,  severite: 'HIGH',        description: "Storage above 80% — clean snapshots or expand storage.", source: 'Proxmox Storage Docs', action: "pvesm status" },
    { metric: 'vm.cpu_pct',      operateur: '>',  seuil: 80,  duree_min: 5,  severite: 'HIGH',        description: "VM CPU > 80% sustained — check for overcommit on hypervisor.", source: 'Proxmox VE Docs', action: "qm monitor {vmid}" },
    { metric: 'vm.ram_pct',      operateur: '>',  seuil: 85,  duree_min: 5,  severite: 'HIGH',        description: "VM RAM > 85% — risk of VM-level swap and application slowdown.", source: 'Proxmox Memory Management', action: "qm monitor {vmid}" },
    { metric: 'vm.statut',       operateur: '=',  seuil: 'down', duree_min: 0, severite: 'CRITICAL',  description: "VM stopped unexpectedly — check HA status and corosync logs.", source: 'Proxmox HA Docs', action: "qm status {vmid} && pvecm status" },
    { metric: 'cluster.quorum',  operateur: '=',  seuil: 'lost', duree_min: 0, severite: 'CRITICAL',  description: "Cluster quorum lost — risk of split-brain, VMs may stop.", source: 'Proxmox Cluster Docs', action: "pvecm status && corosync-cfgtool -s" },
    { metric: 'lstm.score',      operateur: '>',  seuil: 0.8, duree_min: 0,  severite: 'CRITICAL',    description: "AI anomaly score > 0.8 — highly abnormal cluster behavior detected.", source: 'OpsPilot AI Engine', action: "" },
    { metric: 'lstm.score',      operateur: '>',  seuil: 0.5, duree_min: 0,  severite: 'MONITORING',  description: "AI anomaly score > 0.5 — suspicious pattern, enhanced monitoring active.", source: 'OpsPilot AI Engine', action: "" },
    // ── Nouvelles ressources ───────────────────────────────────────────────
    { metric: 'node.swap_pct',       operateur: '>', seuil: 80,  duree_min: 5,  severite: 'CRITICAL',    description: "Swap > 80% means RAM is already full — system is using slow disk as RAM. Performance will be catastrophic.", source: 'Linux Memory Management', action: "free -h && swapon --show" },
    { metric: 'node.swap_pct',       operateur: '>', seuil: 50,  duree_min: 10, severite: 'HIGH',        description: "Swap > 50% — memory pressure building. Identify top memory consumers before saturation.", source: 'Linux Best Practices', action: "ps aux --sort=-%mem | head -15" },
    { metric: 'node.cpu_iowait_pct', operateur: '>', seuil: 30,  duree_min: 5,  severite: 'CRITICAL',    description: "CPU I/O wait > 30% — CPU is blocked waiting for disk. Storage is the bottleneck, not CPU.", source: 'Linux Performance Analysis', action: "iostat -x 1 5" },
    { metric: 'node.cpu_iowait_pct', operateur: '>', seuil: 15,  duree_min: 10, severite: 'HIGH',        description: "CPU I/O wait > 15% — disk pressure detected. Check storage throughput.", source: 'Linux Performance Analysis', action: "iotop -o" },
    { metric: 'node.disk_latency_ms',operateur: '>', seuil: 50,  duree_min: 0,  severite: 'CRITICAL',    description: "Disk latency > 50ms — extremely slow storage. SSD may be failing or ZFS pool degraded.", source: 'Storage Best Practices', action: "zpool status && iostat -x 1" },
    { metric: 'node.disk_latency_ms',operateur: '>', seuil: 10,  duree_min: 5,  severite: 'HIGH',        description: "Disk latency > 10ms — storage degradation. Normal SSD latency is < 1ms.", source: 'Storage Best Practices', action: "iostat -x 1 5" },
    { metric: 'node.cpu_temp_c',     operateur: '>', seuil: 85,  duree_min: 0,  severite: 'CRITICAL',    description: "CPU temperature > 85°C — automatic throttling engaged. CPU running at reduced speed. Check cooling.", source: 'Intel/AMD Thermal Specs', action: "sensors | grep -i temp" },
    { metric: 'node.cpu_temp_c',     operateur: '>', seuil: 75,  duree_min: 10, severite: 'HIGH',        description: "CPU temperature > 75°C — approaching thermal limit. Verify fan operation and airflow.", source: 'Intel/AMD Thermal Specs', action: "sensors" },
    { metric: 'disk.smart_sectors',  operateur: '>', seuil: 0,   duree_min: 0,  severite: 'CRITICAL',    description: "SMART reallocated sectors detected — disk is physically degraded and moving data from bad sectors. Plan immediate replacement.", source: 'SMART Monitoring', action: "smartctl -a /dev/sda" },
    { metric: 'disk.uncorrectable',  operateur: '>', seuil: 0,   duree_min: 0,  severite: 'CRITICAL',    description: "SMART uncorrectable errors — data loss has occurred or is imminent. EMERGENCY: backup immediately.", source: 'SMART Monitoring', action: "smartctl -H /dev/sda && smartctl -l selftest /dev/sda" },
    { metric: 'zfs.arc_hit_rate',    operateur: '<', seuil: 70,  duree_min: 30, severite: 'HIGH',        description: "ZFS ARC hit rate < 70% — most I/O requests go to physical disk. Adding RAM will dramatically improve performance.", source: 'ZFS Best Practices', action: "arc_summary || cat /proc/spl/kstat/zfs/arcstats" },
    { metric: 'net.errors_per_sec',  operateur: '>', seuil: 10,  duree_min: 5,  severite: 'HIGH',        description: "Network interface errors > 10/s — hardware issue: check cable, switch port, or NIC driver.", source: 'Network Diagnostics', action: "ip -s link show && ethtool eth0" },
    { metric: 'corosync.quorum',     operateur: '=', seuil: 'lost', duree_min: 0, severite: 'CRITICAL',  description: "Proxmox cluster quorum lost — all VMs will automatically stop to prevent split-brain data corruption.", source: 'Proxmox Cluster Docs', action: "pvecm status && corosync-cfgtool -s" },
  ]

  const regles = reglesDynamiques.length > 0
    ? reglesDynamiques.map(r => ({
        metric:      r.metric,
        operateur:   r.operateur || '>',
        seuil:       r.seuil,
        duree_min:   r.duree_min || 0,
        severite:    r.severite,
        description: r.description,
        source:      r.source || 'AI Generated',
        action:      r.action || '',
      }))
    : reglesFallback

  const isAI = reglesDynamiques.length > 0

  const regenerer = async () => {
    setRegenerating(true)
    try {
      const r = await fetch('/api/regles/regenerer', { method: 'POST' })
      if (r.ok) {
        setRegenerated(true)
        setTimeout(() => setRegenerated(false), 3000)
      }
    } catch {}
    setRegenerating(false)
  }

  // Couleurs par sévérité
  const sevColor = (s='') => ({
    CRITICAL:'#ef4444', CRITIQUE:'#ef4444',
    HIGH:'#f97316', IMPORTANT:'#f97316',
    MONITORING:'#eab308', SURVEILLANCE:'#eab308',
  })[s.toUpperCase()] || '#64748b'

  // Grouper par sévérité
  const critical   = regles.filter(r => ['CRITICAL','CRITIQUE'].includes(r.severite?.toUpperCase()))
  const high       = regles.filter(r => ['HIGH','IMPORTANT'].includes(r.severite?.toUpperCase()))
  const monitoring = regles.filter(r => ['MONITORING','SURVEILLANCE'].includes(r.severite?.toUpperCase()))

  const RuleCard = ({ rule, idx }) => {
    const color    = sevColor(rule.severite)
    const dureeStr = rule.duree_min > 0 ? ` for ${rule.duree_min}min` : ''
    const isScore  = rule.metric?.includes('score') || rule.metric?.includes('lstm')
    const isStatus = typeof rule.seuil === 'string'
    const seuilStr = isStatus
      ? `${rule.operateur} ${rule.seuil}${dureeStr}`
      : isScore
        ? `${rule.operateur} ${rule.seuil}${dureeStr}`
        : `${rule.operateur} ${rule.seuil}%${dureeStr}`

    // ID unique pour chaque règle — solution CSS pure sans state React
    const checkId = `rule_toggle_${idx}`

    return (
      <div style={{ borderRadius:8, overflow:'hidden', border:`1px solid ${C.border}`, transition:'border-color 0.15s' }}>
        <style>{`
          #${checkId} { display: none; }
          #${checkId}:checked ~ .rule-detail-${idx} { max-height: 400px; }
          #${checkId}:checked ~ .rule-detail-${idx} { border-top: 1px solid ${C.border}; }
          label[for="${checkId}"] .rule-arrow { transition: transform 0.25s; }
          #${checkId}:checked ~ * label[for="${checkId}"] .rule-arrow { transform: rotate(180deg); }
          .rule-header-${idx}:hover { background: ${C.cardH} !important; }
        `}</style>

        {/* Checkbox cachée — gère l'ouverture/fermeture sans JS */}
        <input type="checkbox" id={checkId}/>

        {/* Header cliquable — label HTML natif, 100% fiable */}
        <label
          htmlFor={checkId}
          className={`rule-header-${idx}`}
          style={{
            display:'flex', justifyContent:'space-between', alignItems:'center',
            padding:'12px 16px', cursor:'pointer', userSelect:'none',
            background: C.card, transition:'background 0.15s',
          }}
        >
          <div style={{ display:'flex', alignItems:'center', gap:12 }}>
            <div style={{ width:3, height:36, background:color, borderRadius:2, flexShrink:0 }}/>
            <div>
              <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
                <code style={{ fontSize:12, fontFamily:'JetBrains Mono,monospace', color:'#7dd3fc', background:C.bg, padding:'2px 8px', borderRadius:4 }}>
                  {rule.metric}
                </code>
                <span style={{ fontSize:13, color:C.text, fontFamily:'JetBrains Mono,monospace', fontWeight:600 }}>
                  {seuilStr}
                </span>
              </div>
              <div style={{ fontSize:11, color:C.muted, marginTop:4, fontFamily:'JetBrains Mono,monospace' }}>
                SOURCE: {rule.source}
              </div>
            </div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:8, flexShrink:0 }}>
            <span style={{ padding:'3px 10px', borderRadius:12, fontSize:10, fontWeight:700, letterSpacing:'0.08em', background:color+'18', border:`1px solid ${color}40`, color }}>
              {normalizeSeverity(rule.severite)}
            </span>
            <span style={{ padding:'3px 10px', borderRadius:12, fontSize:10, fontWeight:700, letterSpacing:'0.08em', background:isAI?'#a855f718':'#3b82f618', border:`1px solid ${isAI?'#a855f740':'#3b82f640'}`, color:isAI?'#a855f7':'#3b82f6' }}>
              {isAI ? 'AI' : 'DOC'}
            </span>
            <span className="rule-arrow" style={{ color:C.muted, fontSize:11, display:'inline-block' }}>▼</span>
          </div>
        </label>

        {/* Contenu — géré par CSS uniquement, max-height transition */}
        <div
          className={`rule-detail-${idx}`}
          style={{
            maxHeight:'0px', overflow:'hidden',
            transition:'max-height 0.3s ease',
            background: C.card,
          }}
        >
          <div style={{ padding:'12px 16px 16px 16px' }}>
            <p style={{ fontSize:13, color:C.sub, lineHeight:1.8, margin:'0 0 12px 0' }}>
              {rule.description}
            </p>
            {rule.action && (
              <div>
                <div style={{ fontSize:10, color:C.muted, fontFamily:'JetBrains Mono,monospace', marginBottom:6, letterSpacing:'0.08em' }}>
                  DIAGNOSTIC COMMAND
                </div>
                <pre style={{ background:'#030812', borderRadius:6, padding:'10px 14px', fontSize:12, fontFamily:'JetBrains Mono,monospace', color:'#7dd3fc', margin:0, overflowX:'auto', whiteSpace:'pre-wrap' }}>
                  {rule.action}
                </pre>
              </div>
            )}
          </div>
        </div>
      </div>
    )
  }

    const SevSection = ({ title, color, rules }) => {
    if (!rules.length) return null
    return (
      <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, padding:'4px 0' }}>
          <div style={{ width:8, height:8, borderRadius:'50%', background:color }}/>
          <span style={{ fontSize:10, fontWeight:700, letterSpacing:'0.12em', color, fontFamily:'JetBrains Mono,monospace' }}>{title} ({rules.length})</span>
          <div style={{ flex:1, height:1, background:color+'20' }}/>
        </div>
        {rules.map((r, i) => <RuleCard key={i} idx={`${title}_${i}`} rule={r}/>)}
      </div>
    )
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:20 }}>

      {/* Header */}
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          <h2 style={{ fontSize:18, fontWeight:700, color:C.text, marginBottom:4 }}>Monitoring Rules</h2>
          <div style={{ fontSize:12, color:C.sub }}>
            {isAI
              ? `${regles.length} rules generated by AI · Click any rule to expand`
              : `${regles.length} rules based on Proxmox VE official documentation · Click to expand`}
          </div>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          {isAI && (
            <div style={{ padding:'4px 12px', borderRadius:20, fontSize:11, fontWeight:700, background:'#a855f718', border:'1px solid #a855f740', color:'#a855f7' }}>
              AI GENERATED
            </div>
          )}
          <button
            onClick={regenerer}
            disabled={regenerating}
            style={{
              display:'flex', alignItems:'center', gap:6,
              padding:'7px 16px', borderRadius:8, border:`1px solid ${C.borderHi}`,
              background: regenerated ? '#22c55e18' : C.card,
              color: regenerated ? '#22c55e' : C.sub,
              cursor:'pointer', fontSize:12, fontFamily:'JetBrains Mono,monospace',
              transition:'all 0.2s',
            }}
            onMouseEnter={e => { if (!regenerating) { e.currentTarget.style.borderColor='#a855f7'; e.currentTarget.style.color='#a855f7' }}}
            onMouseLeave={e => { e.currentTarget.style.borderColor=C.borderHi; e.currentTarget.style.color=regenerated?'#22c55e':C.sub }}
          >
            <span style={{ display:'inline-block', animation:regenerating?'spin 1s linear infinite':'none' }}>↺</span>
            {regenerating ? 'Generating...' : regenerated ? '✓ Generated' : 'Regenerate with AI'}
          </button>
        </div>
      </div>

      {/* Info banner */}
      <div style={{ background:'#3b82f608', border:'1px solid #3b82f620', borderRadius:8, padding:'10px 16px', display:'flex', gap:12, alignItems:'flex-start' }}>
        <span style={{ color:'#3b82f6', flexShrink:0, marginTop:1 }}>ℹ</span>
        <div style={{ fontSize:12, color:C.sub, lineHeight:1.7 }}>
          These rules define <strong style={{ color:C.text }}>when OpsPilot triggers an alert</strong>. 
          They are applied continuously to the real-time cluster metrics. 
          Click <strong style={{ color:C.text }}>Regenerate with AI</strong> to have the LLM analyze your cluster baseline and generate adapted thresholds.
          Click any rule to see the diagnostic command.
        </div>
      </div>

      {/* Règles groupées par sévérité */}
      <SevSection title="CRITICAL" color="#ef4444" rules={critical}/>
      <SevSection title="HIGH"     color="#f97316" rules={high}/>
      <SevSection title="MONITORING" color="#eab308" rules={monitoring}/>
    </div>
  )
}

function PageRecommendations({ suggestions }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div><h2 style={{ fontSize:18, fontWeight:700, color:C.text, marginBottom:4 }}>AI Recommendations</h2><div style={{ fontSize:12, color:C.sub }}>Actionable remediation steps from infrastructure analysis</div></div>
      {suggestions.length===0 ? (
        <Card style={{ padding:'50px 0', textAlign:'center' }}><div style={{ fontSize:36, marginBottom:14 }}>◈</div><div style={{ fontSize:14, color:C.sub }}>No recommendations at this time</div></Card>
      ) : suggestions.map((s,i)=>(
        <Card key={i} glow={severityColor(s.severity)} style={{ padding:'16px 18px' }}>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start', marginBottom:10 }}>
            <div style={{ fontSize:15, fontWeight:700, color:C.text, flex:1, marginRight:16 }}>{s.title}</div>
            <div style={{ display:'flex', gap:8 }}><Chip label={normalizeSeverity(s.severity)} color={severityColor(s.severity)}/><Chip label={s.status||'OPEN'} color={s.status==='RESOLVED'?C.green:C.yellow}/></div>
          </div>
          <MD text={s.description || ""}/>
        </Card>
      ))}
    </div>
  )
}

function PageSystemLog({ agentLog }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.scrollTo(0, ref.current.scrollHeight) }, [agentLog])
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16, height:'100%' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div><h2 style={{ fontSize:18, fontWeight:700, color:C.text, marginBottom:4 }}>System Audit Log</h2><div style={{ fontSize:12, color:C.sub }}>Full AI agent execution history</div></div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}><span style={{ width:8, height:8, borderRadius:'50%', background:C.green, display:'inline-block', animation:'pulse 1.5s infinite' }}/><span style={{ fontSize:11, color:C.green, fontFamily:'JetBrains Mono, monospace' }}>LIVE</span></div>
      </div>
      <Card style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', padding:0 }}>
        <div style={{ padding:'10px 16px', borderBottom:`1px solid ${C.border}`, background:C.bg, display:'grid', gridTemplateColumns:'70px 110px 1fr', gap:8, fontSize:10, fontFamily:'JetBrains Mono, monospace', color:C.muted }}>
          <span>TIME</span><span>COMPONENT</span><span>EVENT</span>
        </div>
        <div ref={ref} style={{ flex:1, overflowY:'auto', maxHeight:'calc(100vh - 300px)' }}>
          {agentLog.map((l,i)=>(
            <div key={i} style={{ display:'grid', gridTemplateColumns:'70px 110px 1fr', gap:8, padding:'5px 16px', fontSize:12, fontFamily:'JetBrains Mono, monospace', background:i%2===0?'transparent':C.bg+'60' }}>
              <span style={{ color:C.muted }}>{l.time}</span>
              <span style={{ color:l.color||C.sub, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>{l.label}</span>
              <span style={{ color:C.sub }}>{l.msg}</span>
            </div>
          ))}
          {agentLog.length===0 && <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'40px 0', fontStyle:'italic' }}>Waiting for activity agent...</div>}
        </div>
      </Card>
    </div>
  )
}

// ── CORRECTION 2 : Page d'accueil assistant — propre, sans texte descriptif ─
const QUICK = [
  { label: 'Créer une VM web nginx',        q: 'Je veux créer une VM nginx sur pve1. Quelles ressources me recommandes-tu ?' },
  { label: 'VM base de données PostgreSQL',  q: 'Je veux une VM PostgreSQL sur pve2 pour ~100 connexions. Dimensionne-la.' },
  { label: 'VM Docker',                      q: 'Je veux déployer Docker sur une VM Proxmox. Quelle config optimale ?' },
  { label: 'Migration VM sans downtime',     q: 'Comment migrer linux-vm1 de pve1 vers pve2 sans interruption ?' },
  { label: 'Optimiser la mémoire cluster',   q: "Comment optimiser l'utilisation mémoire sur les VMs du cluster ?" },
  { label: 'Backup Proxmox',                 q: 'Quelle est la meilleure stratégie de backup pour ce cluster ?' },
  { label: 'VM nœud Kubernetes',             q: 'Je veux créer un nœud Kubernetes sur Proxmox. Ressources recommandées ?' },
  { label: 'Diagnostic CPU élevé',           q: 'Une VM a un CPU élevé en permanence. Comment diagnostiquer ?' },
]

function UserMessage({ msg, onEdit, thinking }) {
  const [hovered, setHovered] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editVal, setEditVal] = useState(msg.content)
  const taRef = useRef(null)
  useEffect(() => { if (editing && taRef.current) { taRef.current.focus(); taRef.current.style.height='auto'; taRef.current.style.height=taRef.current.scrollHeight+'px' } }, [editing])
  const handleSubmitEdit = () => { const v=editVal.trim(); if(!v||v===msg.content){setEditing(false);return}; onEdit(msg.id,v); setEditing(false) }
  return (
    <div style={{ display:'flex', gap:12, padding:'8px 0', flexDirection:'row-reverse', alignItems:'flex-start', animation:'fadeUp 0.3s ease' }} onMouseEnter={()=>setHovered(true)} onMouseLeave={()=>setHovered(false)}>
      {hovered && !editing && !thinking && (
        <button onClick={()=>{setEditVal(msg.content);setEditing(true)}} title="Modifier ce message"
          style={{ width:28, height:28, borderRadius:7, border:`1px solid ${C.borderHi}`, background:C.surface, color:C.sub, cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center', fontSize:13, flexShrink:0, marginTop:4, transition:'all 0.15s' }}
          onMouseEnter={e=>{e.currentTarget.style.borderColor=C.blue;e.currentTarget.style.color=C.blue}}
          onMouseLeave={e=>{e.currentTarget.style.borderColor=C.borderHi;e.currentTarget.style.color=C.sub}}
        >✏</button>
      )}
      <div style={{ maxWidth:'78%', background:'#0a1828', border:`1px solid ${editing?C.blue+'60':C.blue+'30'}`, borderRadius:'14px 3px 14px 14px', padding:editing?'10px 12px':'12px 16px', transition:'border-color 0.2s' }}>
        {editing ? (
          <>
            <textarea ref={taRef} value={editVal}
              onChange={e=>{setEditVal(e.target.value);e.target.style.height='auto';e.target.style.height=e.target.scrollHeight+'px'}}
              onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();handleSubmitEdit()};if(e.key==='Escape'){setEditing(false);setEditVal(msg.content)}}}
              style={{ width:'100%', background:'transparent', border:'none', outline:'none', color:C.text, fontSize:13, lineHeight:1.6, fontFamily:"'Inter',sans-serif", resize:'none', minHeight:40 }}
            />
            <div style={{ display:'flex', gap:8, marginTop:10, justifyContent:'flex-end' }}>
              <button onClick={()=>{setEditing(false);setEditVal(msg.content)}} style={{ padding:'4px 12px', borderRadius:6, border:`1px solid ${C.border}`, background:'transparent', color:C.sub, cursor:'pointer', fontSize:12 }}>Annuler</button>
              <button onClick={handleSubmitEdit} style={{ padding:'4px 14px', borderRadius:6, border:'none', background:C.blue, color:'#fff', cursor:'pointer', fontSize:12, fontWeight:600 }}>Envoyer ↑</button>
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize:13, color:C.text, lineHeight:1.75 }}>{msg.content}</div>
            <div style={{ fontSize:10, color:C.muted, marginTop:8, fontFamily:'JetBrains Mono, monospace', display:'flex', gap:8, alignItems:'center' }}>
              {msg.timestamp?.slice(11,19)}
              {msg.edited && <span style={{ color:C.muted, fontSize:9 }}>✏ modifié</span>}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

function PageAssistant({ messages, thinking, input, setInput, onSend, onEdit, onClear, connected }) {
  const endRef = useRef(null)
  useEffect(() => { endRef.current?.scrollIntoView({ behavior:'smooth' }) }, [messages, thinking])
  return (
    <div style={{ display:'flex', flexDirection:'column', height:'100%' }}>
      <div style={{ flex:1, overflowY:'auto', paddingBottom:16 }}>

        {/* CORRECTION 2 : Écran d'accueil épuré — logo + boutons seulement */}
        {messages.length === 0 && (
          <div style={{ maxWidth:660, margin:'40px auto', animation:'fadeUp 0.4s ease' }}>
            <div style={{ textAlign:'center', marginBottom:28 }}>
              <div style={{ width:52, height:52, borderRadius:14, background:C.blue, display:'flex', alignItems:'center', justifyContent:'center', fontSize:24, margin:'0 auto 16px', boxShadow:`0 0 24px ${C.blue}40` }}>⬡</div>
              <div style={{ fontSize:22, fontWeight:800, color:C.text, letterSpacing:'-0.02em' }}>OpsPilot</div>
            </div>
            <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
              {QUICK.map((a,i)=>(
                <button key={i} onClick={()=>{ setInput(a.q); setTimeout(()=>onSend(a.q),50) }}
                  style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:'12px 16px', cursor:'pointer', textAlign:'left', fontSize:12, color:C.sub, lineHeight:1.5, transition:'all 0.15s', display:'flex', alignItems:'center', gap:10 }}
                  onMouseEnter={e=>{e.currentTarget.style.borderColor=C.blue;e.currentTarget.style.color=C.text}}
                  onMouseLeave={e=>{e.currentTarget.style.borderColor=C.border;e.currentTarget.style.color=C.sub}}
                >
                  <span style={{ color:C.muted, fontSize:14 }}>→</span>{a.label}
                </button>
              ))}
            </div>
          </div>
        )}

        <div style={{ maxWidth:800, margin:'0 auto' }}>
          {messages.map((msg,i)=>{
            const isUser=msg.role==='user', isAlert=msg.type==='alerte', isSys=msg.type==='systeme'
            if(isSys) return (
              <div key={msg.id||i} style={{ textAlign:'center', padding:'10px 0', animation:'fadeUp 0.3s ease' }}>
                <div style={{ display:'inline-block', background:C.card, border:`1px solid ${C.border}`, borderRadius:20, padding:'6px 18px', fontSize:12, color:C.sub }}>
                  <MD text={msg.content} small/>
                </div>
              </div>
            )
            if(isUser) return <UserMessage key={msg.id||i} msg={msg} onEdit={onEdit} thinking={thinking}/>
            return (
              <div key={msg.id||i} style={{ display:'flex', gap:12, padding:'8px 0', alignItems:'flex-start', animation:'fadeUp 0.3s ease' }}>
                <div style={{ width:34, height:34, borderRadius:9, flexShrink:0, marginTop:2, background:isAlert?'#180808':'#080e22', border:`1px solid ${isAlert?C.red+'40':C.border}`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:15 }}>{isAlert?'⚠':'⬡'}</div>
                <div style={{ maxWidth:'78%', background:isAlert?'#110606':C.card, border:`1px solid ${isAlert?C.red+'30':C.border}`, borderRadius:'3px 14px 14px 14px', padding:'12px 16px' }}>
                  {isAlert && (
                    <div style={{ display:'flex', alignItems:'center', gap:8, marginBottom:10, paddingBottom:10, borderBottom:`1px solid ${C.red}20` }}>
                      <span style={{ fontSize:11, fontWeight:700, color:C.red, fontFamily:'JetBrains Mono, monospace', letterSpacing:'0.08em' }}>⚠ AUTOMATED INCIDENT ALERT</span>
                      {msg.anomalies?.map((a,j)=><Chip key={j} label={normalizeSeverity(a.niveau)} color={severityColor(a.niveau)}/>)}
                    </div>
                  )}
                  <MD text={msg.content}/>
                  <div style={{ fontSize:10, color:C.muted, marginTop:8, textAlign:'right', fontFamily:'JetBrains Mono, monospace', display:'flex', justifyContent:'flex-end', gap:10, alignItems:'center' }}>
                    {msg.llm && <span style={{ color:msg.llm==='claude'?C.purple:C.cyan, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>◆ {(msg.model||msg.llm||'groq').toUpperCase()}</span>}
                    {msg.timestamp?.slice(11,19)}
                    {msg.rapport && <span style={{ color:C.yellow }}>📄 {msg.rapport}</span>}
                  </div>
                </div>
              </div>
            )
          })}
          {thinking && (
            <div style={{ display:'flex', gap:12, padding:'8px 0', animation:'fadeUp 0.2s ease' }}>
              <div style={{ width:34, height:34, borderRadius:9, background:'#080e22', border:`1px solid ${C.border}`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:15, flexShrink:0 }}>⬡</div>
              <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:'3px 14px 14px 14px', padding:'14px 18px', display:'flex', gap:5, alignItems:'center' }}>
                {[0,1,2].map(j=><span key={j} style={{ width:6, height:6, borderRadius:'50%', background:C.muted, display:'inline-block', animation:`pulse 1.2s infinite ${j*0.2}s` }}/>)}
                <span style={{ fontSize:11, color:C.muted, marginLeft:8, fontFamily:'JetBrains Mono, monospace' }}>en cours...</span>
              </div>
            </div>
          )}
          <div ref={endRef}/>
        </div>
      </div>

      <div style={{ borderTop:`1px solid ${C.border}`, paddingTop:16, flexShrink:0 }}>
        <div style={{ maxWidth:800, margin:'0 auto' }}>
          <div style={{ display:'flex', gap:10, background:C.card, border:`1px solid ${C.border}`, borderRadius:12, padding:'10px 14px' }}
            onFocusCapture={e=>e.currentTarget.style.borderColor=C.blue}
            onBlurCapture={e=>e.currentTarget.style.borderColor=C.border}
          >
            <textarea value={input} onChange={e=>setInput(e.target.value)}
              onKeyDown={e=>{if(e.key==='Enter'&&!e.shiftKey){e.preventDefault();onSend()}}}
              placeholder="Question sur ton infrastructure Proxmox... (Entrée pour envoyer)"
              rows={1}
              style={{ flex:1, background:'transparent', border:'none', outline:'none', color:C.text, fontSize:13, lineHeight:1.6, fontFamily:"'Inter',sans-serif", resize:'none', maxHeight:120, overflowY:'auto' }}
              onInput={e=>{e.target.style.height='auto';e.target.style.height=Math.min(e.target.scrollHeight,120)+'px'}}
            />
            <button onClick={()=>onSend()} disabled={!input.trim()||thinking||!connected}
              style={{ width:36, height:36, borderRadius:8, border:'none', flexShrink:0, background:input.trim()&&!thinking&&connected?C.blue:C.surface, color:'#fff', cursor:input.trim()&&!thinking&&connected?'pointer':'default', display:'flex', alignItems:'center', justifyContent:'center', fontSize:16, transition:'background 0.2s' }}>
              {thinking?<span style={{ width:14, height:14, border:`2px solid ${C.muted}`, borderTopColor:C.text, borderRadius:'50%', display:'inline-block', animation:'spin 0.8s linear infinite' }}/>:'↑'}
            </button>
          </div>
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:8 }}>
            <div style={{ fontSize:11, color:C.muted }}>✏ Modifiable au survol · Infra Proxmox/VM</div>
            {messages.filter(m=>m.role==='user'||m.role==='assistant').length>0 && (
              <button onClick={onClear}
                style={{ fontSize:11, color:C.muted, background:'transparent', border:`1px solid ${C.border}`, borderRadius:6, padding:'3px 10px', cursor:'pointer', transition:'all 0.15s' }}
                onMouseEnter={e=>{e.currentTarget.style.borderColor=C.red;e.currentTarget.style.color=C.red}}
                onMouseLeave={e=>{e.currentTarget.style.borderColor=C.border;e.currentTarget.style.color=C.muted}}
              >🗑 Effacer</button>
            )}
          </div>
        </div>
      </div>
    </div>
  )
}

function ReportsPanel({ rapports, onOpen }) {
  if(!rapports.length) return <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'24px 0' }}>No reports yet</div>
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
      {rapports.map((r,i)=>{
        const isCrit=r.nom.includes('critique')||r.nom.includes('critique'), isImp=r.nom.includes('important')
        const ac=isCrit?C.red:isImp?C.orange:C.green
        return (
          <div key={i} onClick={()=>onOpen(r)} style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:'10px 12px', cursor:'pointer', transition:'border-color 0.15s' }}
            onMouseEnter={e=>e.currentTarget.style.borderColor=ac}
            onMouseLeave={e=>e.currentTarget.style.borderColor=C.border}
          >
            <div style={{ fontSize:11, color:C.sub, fontFamily:'JetBrains Mono, monospace', marginBottom:4, wordBreak:'break-all' }}>{r.nom}</div>
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:C.muted, fontFamily:'JetBrains Mono, monospace' }}>
              <span style={{ color:ac }}>{isCrit?'● CRITICAL':isImp?'● IMPORTANT':'● INFO'}</span>
              <span>{r.date?.slice(11,19)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}

function Placeholder({ icon='◈', text }) {
  return <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:300 }}><div style={{ textAlign:'center', color:C.muted }}><div style={{ fontSize:36, marginBottom:12 }}>{icon}</div><div style={{ fontSize:14 }}>{text}</div></div></div>
}

const PAGES = [
  { id:'dashboard',       icon:'◈', label:'Dashboard',        desc:'Overview & KPIs' },
  { id:'infrastructure',  icon:'◉', label:'Infrastructure',   desc:'Servers & VMs' },
  { id:'incidents',       icon:'◬', label:'Incidents',        desc:'Detected issues' },
  { id:'rules',           icon:'⚙', label:'Monitoring Rules', desc:'AI-generated rules' },
  { id:'recommendations', icon:'◆', label:'Recommendations',  desc:'Remediation steps' },
  { id:'log',             icon:'▶', label:'System Log',       desc:'Audit trail' },
  { id:'assistant',       icon:'⬡', label:'AI Assistant',     desc:'Infrastructure chat' },
]

export default function App() {
  const { connected, send, handlerRef } = useWebSocket(`ws://${window.location.host}/ws`)
  const [page,         setPage]         = useState('dashboard')
  const [cluster,      setCluster]      = useState(null)
  const [history,      setHistory]      = useState([])
  const [incidents,    setIncidents]    = useState([])
  const [suggestions,  setSuggestions]  = useState([])
  const [agentLog,     setAgentLog]     = useState([])
  const [chatMessages, setChatMessages] = useState([])
  const [chatInput,    setChatInput]    = useState('')
  const [thinking,     setThinking]     = useState(false)
  const [uptime,       setUptime]       = useState(0)
  const [rapports,     setRapports]     = useState([])
  const [dernierLstm,  setDernierLstm]  = useState({ score:0, seuil:0.5, score_if:0, score_lstm:0, lstm_ready:false, drift:false })
  const [reglesDyn,   setReglesDyn]    = useState([])
  const [showReports,  setShowReports]  = useState(false)
  const [openReport,   setOpenReport]   = useState(null)

  const logTime = () => new Date().toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit', second:'2-digit' })
  const addLog  = useCallback((label, msg, color=C.sub) => { setAgentLog(l=>[...l,{time:logTime(),label,msg,color}]) }, [])

  handlerRef.current = useCallback((data) => {
    if (data.type==='thinking') { setThinking(true); return }
    if (['reponse','message_edite','history_cleared','message_user','systeme','alerte','historique'].includes(data.type)) setThinking(false)
    if (data.type==='etat_cluster') {
      setCluster(data.etat)
      setHistory(h=>[...h,{cpu:data.etat?.noeuds?.[0]?.cpu_pct??0,ram:data.etat?.noeuds?.[0]?.ram_pct??0}].slice(-60))
      // Mettre à jour le score ML si présent dans le message
      if (data.lstm) setDernierLstm(prev => ({...prev, ...data.lstm}))
      const nb=data.etat?.alertes?.length??0
      if(nb>0) addLog('Monitoring',`${nb} alert(s) active`,C.orange)
      else addLog('Monitoring',`Cluster healthy — ${data.etat?.vms_running??0} VMs running`,C.green)
      return
    }
    if (data.type==='alerte') {
      // Mettre à jour le score ML depuis l'alerte
      if (data.lstm) setDernierLstm(prev => ({...prev, ...data.lstm}))
      setChatMessages(m=>[...m,{...data,id:data.id||`alert_${Date.now()}`}])
      setSuggestions(s=>{
        // Parser les 2 sections du rapport LLM
        const raw = data.content || ''
        const incidentPart = raw.includes('---RECOMMENDATION---')
          ? raw.split('---RECOMMENDATION---')[0].replace('---INCIDENT---','').trim()
          : raw
        const recoPart = raw.includes('---RECOMMENDATION---')
          ? raw.split('---RECOMMENDATION---')[1].trim()
          : raw

        // Extraire le titre de la recommandation
        const titleMatch = recoPart.match(/\*\*Fix title:\*\*\s*(.+)/)
        const recoTitle = titleMatch ? titleMatch[1].trim() : (data.anomalies?.[0]?.message || 'Infrastructure Issue')

        return [...s, {
          title: recoTitle,
          description: recoPart,
          severity: data.anomalies?.[0]?.niveau || 'HIGH',
          status: 'OPEN',
          timestamp: data.timestamp,
          target: data.anomalies?.[0]?.cible || 'cluster',
          rapport: data.rapport,
          incident: incidentPart,
        }]
      })
      setIncidents(a=>{
        const raw = data.content || ''
        const incidentPart = raw.includes('---RECOMMENDATION---')
          ? raw.split('---RECOMMENDATION---')[0].replace('---INCIDENT---','').trim()
          : raw
        return [...a,...(data.anomalies||[]).map(an=>({
          ...an,
          timestamp: data.timestamp,
          score: data.lstm?.score ?? 0.0,
          rapport: data.rapport,
          incident: incidentPart,
          vms: []
        }))]
      })
      addLog('AI Engine',`Incident: ${data.anomalies?.[0]?.message?.slice(0,50)||'anomaly'}`,C.red)
      return
    }
    if (data.type==='reponse') { setChatMessages(m=>[...m,{...data,id:data.id||`rep_${Date.now()}`}]); addLog('AI Assistant','Réponse générée',C.cyan); return }
    if (data.type==='systeme') { setChatMessages(m=>[...m,{...data,id:data.id||`sys_${Date.now()}`}]); return }
    if (data.type==='message_user') { setChatMessages(m=>m.map(msg=>msg.content===data.content&&msg.role==='user'&&!msg.id?{...msg,id:data.id}:msg)); return }
    if (data.type==='message_edite') {
      setChatMessages(m=>{
        const idx=m.findIndex(msg=>msg.id===data.msg_id); if(idx===-1) return m
        const u=[...m]; u[idx]={...u[idx],content:data.content,edited:true}; return u.slice(0,idx+1)
      }); return
    }
    if (data.type==='historique') { setChatMessages(data.messages.map(m=>({...m,type:m.role==='user'?'question':'reponse'}))); return }
    if (data.type==='history_cleared') { setChatMessages([]); return }
  }, [addLog])

  useEffect(() => {
    fetch('/api/cluster').then(r=>r.json()).then(d=>{if(!d.error)setCluster(d)}).catch(()=>{})
    const lr=()=>fetch('/api/rapports').then(r=>r.json()).then(setRapports).catch(()=>{})
    const loadRegles=()=>fetch('/api/regles').then(r=>r.json()).then(d=>{if(d.regles&&d.regles.length>0)setReglesDyn(d.regles)}).catch(()=>{})
    lr(); loadRegles()
    const t1=setInterval(()=>setUptime(u=>u+1),1000), t2=setInterval(lr,15000), t3=setInterval(loadRegles,300000)
    addLog('System','OpsPilot initialized',C.blue)
    addLog('Connectivity','pve1 + pve2 connected',C.green)
    return()=>{clearInterval(t1);clearInterval(t2);clearInterval(t3)}
  }, [])
  useEffect(()=>{if(connected)addLog('Network','Real-time connection established',C.green)},[connected])

  const sendChat = useCallback((text=chatInput)=>{
    const q=(text||chatInput).trim(); if(!q||thinking) return
    const tmpId=`tmp_${Date.now()}`
    setChatMessages(m=>[...m,{id:tmpId,role:'user',content:q,type:'question',timestamp:new Date().toISOString(),edited:false}])
    send({type:'question',content:q}); setChatInput('')
    addLog('AI Assistant',q.slice(0,60)+(q.length>60?'...':''),C.cyan)
  },[chatInput,thinking,send,addLog])

  const editChat  = useCallback((msgId,newContent)=>{ send({type:'edit_message',msg_id:msgId,content:newContent}) },[send])
  const clearChat = useCallback(()=>{ send({type:'clear_history'}) },[send])
  const openReportFile = async(r)=>{ const res=await fetch(`/api/rapports/${r.nom}`); const d=await res.json(); setOpenReport({...r,contenu:d.contenu||''}) }

  const alertCount=cluster?.alertes?.length??0, incidentCount=incidents.length, isHealthy=alertCount===0

  return (
    <div style={{ display:'flex', height:'100vh', background:C.bg, color:C.text, fontFamily:"'Inter','Segoe UI',sans-serif", overflow:'hidden' }}>
      <style>{`
        *{box-sizing:border-box;margin:0;padding:0}
        ::-webkit-scrollbar{width:4px;height:4px}
        ::-webkit-scrollbar-track{background:transparent}
        ::-webkit-scrollbar-thumb{background:#1a2d44;border-radius:2px}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
        @keyframes spin{to{transform:rotate(360deg)}}
        @keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
        textarea{resize:none;outline:none}
        button{font-family:inherit}
      `}</style>

      <nav style={{ width:230, flexShrink:0, background:C.surface, borderRight:`1px solid ${C.border}`, display:'flex', flexDirection:'column' }}>
        <div style={{ padding:'18px 16px', borderBottom:`1px solid ${C.border}` }}>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            <div style={{ width:34, height:34, borderRadius:9, background:C.blue, display:'flex', alignItems:'center', justifyContent:'center', fontSize:17, fontWeight:900, color:'#fff', flexShrink:0 }}>⬡</div>
            <div>
              <div style={{ fontSize:14, fontWeight:800, color:C.text, letterSpacing:'-0.01em' }}>OpsPilot</div>
              <div style={{ fontSize:9, color:C.muted, fontFamily:'JetBrains Mono, monospace', letterSpacing:'0.1em' }}>INFRASTRUCTURE AI</div>
            </div>
          </div>
        </div>
        <div style={{ flex:1, padding:'10px 8px', display:'flex', flexDirection:'column', gap:2, overflowY:'auto' }}>
          {PAGES.map(({ id, icon, label })=>{
            const badge=id==='incidents'?incidentCount:id==='recommendations'?suggestions.length:0, active=page===id
            return (
              <button key={id} onClick={()=>setPage(id)} style={{ display:'flex', alignItems:'center', gap:10, padding:'9px 12px', borderRadius:8, cursor:'pointer', background:active?C.card:'transparent', border:`1px solid ${active?C.borderHi:'transparent'}`, color:active?C.text:C.sub, fontSize:13, textAlign:'left', transition:'all 0.15s', width:'100%', justifyContent:'space-between' }}>
                <div style={{ display:'flex', alignItems:'center', gap:10 }}>
                  <span style={{ color:active?C.blue:C.muted, fontSize:13, width:16, textAlign:'center' }}>{icon}</span>{label}
                </div>
                {badge>0 && <span style={{ background:C.red, color:'#fff', borderRadius:10, padding:'1px 7px', fontSize:10, fontWeight:700, fontFamily:'JetBrains Mono, monospace' }}>{badge}</span>}
              </button>
            )
          })}
        </div>
        <div style={{ padding:'8px 8px 0' }}>
          <button onClick={()=>setShowReports(v=>!v)} style={{ width:'100%', display:'flex', alignItems:'center', gap:10, padding:'9px 12px', borderRadius:8, cursor:'pointer', background:showReports?C.card:'transparent', border:`1px solid ${showReports?C.borderHi:'transparent'}`, color:showReports?C.yellow:C.sub, fontSize:13, transition:'all 0.15s', justifyContent:'space-between' }}>
            <div style={{ display:'flex', alignItems:'center', gap:10 }}><span style={{ color:showReports?C.yellow:C.muted, fontSize:13, width:16, textAlign:'center' }}>📄</span>Incident Reports</div>
            {rapports.length>0 && <span style={{ background:C.yellow, color:'#000', borderRadius:10, padding:'1px 7px', fontSize:10, fontWeight:700 }}>{rapports.length}</span>}
          </button>
          {showReports && <div style={{ padding:'8px 4px', maxHeight:200, overflowY:'auto' }}><ReportsPanel rapports={rapports} onOpen={openReportFile}/></div>}
        </div>
        <div style={{ padding:'14px', borderTop:`1px solid ${C.border}`, marginTop:8 }}>
          <div style={{ fontSize:9, color:C.muted, letterSpacing:'0.1em', fontFamily:'JetBrains Mono, monospace', marginBottom:10 }}>CONNECTED NODES</div>
          {[
            { label:'pve1',      ip:'192.168.138.100', ok:true },
            { label:'pve2',      ip:'192.168.138.101', ok:true },
            { label:'linux-vm1', ip:'192.168.138.133', ok:cluster?.vms?.find(v=>v.nom==='linux-vm1')?.statut==='running' },
            { label:'linux-vm2', ip:'192.168.138.137', ok:cluster?.vms?.find(v=>v.nom==='linux-vm2')?.statut==='running' },
          ].map(({ label, ip, ok })=>(
            <div key={label} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:7 }}>
              <OnlineDot online={ok??false}/>
              <span style={{ fontSize:11, color:C.sub, fontFamily:'JetBrains Mono, monospace', flex:1 }}>{label}</span>
              <span style={{ fontSize:9, color:C.muted, fontFamily:'JetBrains Mono, monospace' }}>{ip.slice(-3)}</span>
            </div>
          ))}
          <div style={{ marginTop:10, paddingTop:10, borderTop:`1px solid ${C.border}`, display:'flex', justifyContent:'space-between', fontSize:10, color:C.muted, fontFamily:'JetBrains Mono, monospace' }}>
            <div style={{ display:'flex', alignItems:'center', gap:5 }}><OnlineDot online={connected} pulse={false}/>{connected?'Live':'Reconnecting'}</div>
            <span>{fmtTime(uptime)}</span>
          </div>
        </div>
      </nav>

      <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden' }}>
        <header style={{ height:52, borderBottom:`1px solid ${C.border}`, background:C.surface, display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 24px', flexShrink:0 }}>
          <div>
            <span style={{ fontWeight:700, fontSize:15, color:C.text }}>{PAGES.find(p=>p.id===page)?.label}</span>
            <span style={{ fontSize:12, color:C.sub, marginLeft:12 }}>{PAGES.find(p=>p.id===page)?.desc}</span>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:16, fontSize:12, fontFamily:'JetBrains Mono, monospace' }}>
            {alertCount>0 && <span style={{ color:C.red, fontWeight:700, cursor:'pointer' }} onClick={()=>setPage('dashboard')}>⚠ {alertCount} Active Alert{alertCount>1?'s':''}</span>}
            <span style={{ color:C.muted }}>{cluster?.vms_running??'—'} VMs · {cluster?.noeuds?.length??'—'} Nodes</span>
            <div style={{ padding:'4px 12px', borderRadius:20, fontSize:11, fontWeight:700, background:(isHealthy?C.green:C.red)+'15', border:`1px solid ${(isHealthy?C.green:C.red)}40`, color:isHealthy?C.green:C.red }}>
              {isHealthy?'● OPERATIONAL':'● INCIDENT ACTIVE'}
            </div>
          </div>
        </header>

        <main style={{ flex:1, overflow:page==='assistant'?'hidden':'auto', padding:'20px 24px', display:'flex', flexDirection:'column' }}>
          {page==='dashboard'       && <PageDashboard       cluster={cluster} history={history} incidents={incidents} agentLog={agentLog} dernier_lstm={dernierLstm}/>}
          {page==='infrastructure'  && <PageInfrastructure  cluster={cluster} history={history}/>}
          {page==='incidents'       && <PageIncidents       incidents={incidents}/>}
          {page==='rules'           && <PageMonitoringRules reglesDynamiques={reglesDyn}/>}
          {page==='recommendations' && <PageRecommendations suggestions={suggestions}/>}
          {page==='log'             && <PageSystemLog       agentLog={agentLog}/>}
          {page==='assistant'       && <PageAssistant messages={chatMessages} thinking={thinking} input={chatInput} setInput={setChatInput} onSend={sendChat} onEdit={editChat} onClear={clearChat} connected={connected}/>}
        </main>
      </div>

      {openReport && (
        <div style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.85)', zIndex:1000, display:'flex', alignItems:'center', justifyContent:'center', padding:32 }} onClick={()=>setOpenReport(null)}>
          <div onClick={e=>e.stopPropagation()} style={{ background:C.card, border:`1px solid ${C.borderHi}`, borderRadius:12, width:'100%', maxWidth:780, maxHeight:'85vh', display:'flex', flexDirection:'column', boxShadow:'0 0 60px rgba(0,0,0,0.8)' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'14px 20px', borderBottom:`1px solid ${C.border}` }}>
              <span style={{ fontFamily:'JetBrains Mono, monospace', fontSize:12, color:C.sub }}>{openReport.nom}</span>
              <button onClick={()=>setOpenReport(null)} style={{ background:'none', border:`1px solid ${C.border}`, color:C.sub, borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:12 }}>✕ Close</button>
            </div>
            <pre style={{ flex:1, overflowY:'auto', margin:0, padding:'20px 24px', fontFamily:'JetBrains Mono, monospace', fontSize:12, lineHeight:1.8, color:C.sub, whiteSpace:'pre-wrap' }}>{openReport.contenu}</pre>
          </div>
        </div>
      )}
    </div>
  )
}