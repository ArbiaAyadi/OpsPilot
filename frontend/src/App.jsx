/**
 * OpsPilot Enterprise — Infrastructure AI Platform
 * Point d'entree : gere le WebSocket, le state global de l'app,
 * la sidebar de navigation, et route vers la bonne page.
 */
import { useState, useCallback, useEffect } from 'react'
import { C } from './utils/colors'
import { fmtTime } from './utils/formatters'
import { useWebSocket } from './hooks/useWebSocket'
import { OnlineDot } from './components/OnlineDot'
import { PageDashboard } from './pages/PageDashboard'
import { PageInfrastructure } from './pages/PageInfrastructure'
import { PageIncidents } from './pages/PageIncidents'
import { PageMonitoringRules } from './pages/PageMonitoringRules'
import { PageRecommendations } from './pages/PageRecommendations'
import { PageSystemLog } from './pages/PageSystemLog'
import { PageAssistant } from './pages/PageAssistant'

// ── Icônes SVG inline propres ──────────────────────────────────────────────
const Icons = {
  overview: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="3" y="3" width="7" height="7" rx="1"/>
      <rect x="14" y="3" width="7" height="7" rx="1"/>
      <rect x="3" y="14" width="7" height="7" rx="1"/>
      <rect x="14" y="14" width="7" height="7" rx="1"/>
    </svg>
  ),
  infrastructure: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <rect x="2" y="2" width="20" height="8" rx="2"/>
      <rect x="2" y="14" width="20" height="8" rx="2"/>
      <line x1="6" y1="6" x2="6.01" y2="6"/>
      <line x1="6" y1="18" x2="6.01" y2="18"/>
    </svg>
  ),
  incidents: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
      <line x1="12" y1="9" x2="12" y2="13"/>
      <line x1="12" y1="17" x2="12.01" y2="17"/>
    </svg>
  ),
  rules: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <circle cx="12" cy="12" r="3"/>
      <path d="M19.07 4.93a10 10 0 010 14.14M4.93 4.93a10 10 0 000 14.14"/>
      <path d="M15.54 8.46a5 5 0 010 7.07M8.46 8.46a5 5 0 000 7.07"/>
    </svg>
  ),
  recommendations: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M12 22c5.523 0 10-4.477 10-10S17.523 2 12 2 2 6.477 2 12s4.477 10 10 10z"/>
      <path d="M9.09 9a3 3 0 015.83 1c0 2-3 3-3 3"/>
      <line x1="12" y1="17" x2="12.01" y2="17"/>
    </svg>
  ),
  log: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/>
      <polyline points="14 2 14 8 20 8"/>
      <line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/>
      <polyline points="10 9 9 9 8 9"/>
    </svg>
  ),
  assistant: (active, color) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? color : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M21 15a2 2 0 01-2 2H7l-4 4V5a2 2 0 012-2h14a2 2 0 012 2z"/>
    </svg>
  ),
  reports: (active) => (
    <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke={active ? '#f59e0b' : '#4a5568'} strokeWidth="1.8" strokeLinecap="round" strokeLinejoin="round">
      <path d="M13 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V9z"/>
      <polyline points="13 2 13 9 20 9"/>
      <line x1="16" y1="13" x2="8" y2="13"/>
      <line x1="16" y1="17" x2="8" y2="17"/>
    </svg>
  ),
}

const PAGES = [
  { id:'dashboard',       icon:'overview',        label:'Overview',         desc:'Cluster status & KPIs'     },
  { id:'infrastructure',  icon:'infrastructure',  label:'Infrastructure',   desc:'Nodes & virtual machines'  },
  { id:'incidents',       icon:'incidents',       label:'Incidents',        desc:'Detected anomalies'        },
  { id:'rules',           icon:'rules',           label:'Monitoring Rules', desc:'AI-generated thresholds'   },
  { id:'recommendations', icon:'recommendations', label:'Recommendations',  desc:'Remediation actions'       },
  { id:'log',             icon:'log',             label:'Audit Log',        desc:'System event trail'        },
  { id:'assistant',       icon:'assistant',       label:'AI Assistant',     desc:'Infrastructure chat'       },
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
  const [reglesDyn,    setReglesDyn]    = useState([])

  const logTime = () => new Date().toLocaleTimeString('fr-FR', { hour:'2-digit', minute:'2-digit', second:'2-digit' })
  const addLog  = useCallback((label, msg, color='#64748b') => { setAgentLog(l=>[...l,{time:logTime(),label,msg,color}]) }, [])

  handlerRef.current = useCallback((data) => {
    if (data.type==='thinking') { setThinking(true); return }
    if (['reponse','message_edite','history_cleared','message_user','systeme','alerte','historique'].includes(data.type)) setThinking(false)
    if (data.type==='etat_cluster') {
      setCluster(data.etat)
      const noeudsSnap = (data.etat?.noeuds || []).map(n => ({ nom: n.nom, cpu: n.cpu_pct??0, ram: n.ram_pct??0 }))
      setHistory(h => [...h, { t: Date.now(), noeuds: noeudsSnap }].slice(-60))
      if (data.lstm) setDernierLstm(prev => ({...prev, ...data.lstm}))
      const nb=data.etat?.alertes?.length??0
      const vmsTotal   = data.etat?.vms?.length ?? 0
      const vmsRunning = data.etat?.vms_running ?? 0
      const vmMsg = vmsTotal > 0 ? `${vmsRunning}/${vmsTotal} VMs running` : 'no VMs detected'
      if(nb>0) addLog('Monitoring',`${nb} alert(s) active`,'#f97316')
      else addLog('Monitoring',`Cluster healthy — ${vmMsg}`,'#22c55e')
      return
    }
    if (data.type==='alerte') {
      if (data.lstm) setDernierLstm(prev => ({...prev, ...data.lstm}))

      // Nettoyer les marqueurs LLM bruts sous toutes leurs formes
      const rawContent = data.content || ''
      const cleanContent = rawContent
        .replace(/---INCIDENT---/g, '')
        .replace(/---RECOMMENDATION---/g, '')
        .replace(/\*\*Severity:\*\*\s*(CRITICAL|HIGH|MONITORING|IMPORTANT|CRITIQUE|SURVEILLANCE)\s*\n?/gi, '')
        .replace(/```bash\s*\nCopy\s*\n/g, '```bash\n')
        .replace(/bashcopier/g, '')
        .replace(/bash\nCopy\n/g, '')
        .replace(/bash\nCopy/g, '')
        .trim()

      // Parser les sections depuis le contenu brut (avant nettoyage complet)
      // pour extraire correctement incident et reco
      const incidentPart = rawContent.includes('---RECOMMENDATION---')
        ? rawContent.split('---RECOMMENDATION---')[0].replace('---INCIDENT---','').trim()
        : rawContent
      const recoPart = rawContent.includes('---RECOMMENDATION---')
        ? rawContent.split('---RECOMMENDATION---')[1].trim()
        : rawContent
      const titleMatch = recoPart.match(/\*\*Fix title:\*\*\s*(.+)/)
      const recoTitle  = titleMatch ? titleMatch[1].trim() : (data.anomalies?.[0]?.message || 'Infrastructure Issue')

      setChatMessages(m=>[...m,{...data, content: cleanContent, id:data.id||`alert_${Date.now()}`}])

      // Récupérer les métriques complètes du nœud concerné (niveaux 1+2+3)
      // pour les injecter dans la carte Recommendations
      const cibleNom   = data.anomalies?.[0]?.cible || ''
      const noeudLive  = cluster?.noeuds?.find(n =>
        n.nom?.toLowerCase() === cibleNom?.toLowerCase() ||
        cibleNom?.toLowerCase().includes(n.nom?.toLowerCase())
      ) || cluster?.noeuds?.[0] || {}

      setSuggestions(s=>[...s, {
        title:       recoTitle,
        description: recoPart,
        severity:    data.anomalies?.[0]?.niveau || 'HIGH',
        status:      'OPEN',
        timestamp:   data.timestamp,
        target:      data.anomalies?.[0]?.cible || 'cluster',
        rapport:     data.rapport,
        incident:    incidentPart,
        // Métriques niveau 1
        cpu_pct:     noeudLive.cpu_pct     ?? null,
        ram_pct:     noeudLive.ram_pct     ?? null,
        disk_pct:    noeudLive.disk_pct    ?? null,
        ram_used_gb: noeudLive.ram_used_gb ?? null,
        ram_total_gb:noeudLive.ram_total_gb?? null,
        // Métriques niveau 2
        swap_pct:              noeudLive.swap_pct              ?? null,
        swap_used_gb:          noeudLive.swap_used_gb          ?? null,
        cpu_iowait_pct:        noeudLive.cpu_iowait_pct        ?? null,
        disk_read_latency_ms:  noeudLive.disk_read_latency_ms  ?? null,
        disk_write_latency_ms: noeudLive.disk_write_latency_ms ?? null,
        disk_read_iops:        noeudLive.disk_read_iops        ?? null,
        disk_write_iops:       noeudLive.disk_write_iops       ?? null,
        net_errors_in:         noeudLive.net_errors_in         ?? null,
        net_errors_out:        noeudLive.net_errors_out        ?? null,
        net_drop_in:           noeudLive.net_drop_in           ?? null,
        net_drop_out:          noeudLive.net_drop_out          ?? null,
        // Métriques niveau 3
        cpu_temp_max_c:          noeudLive.cpu_temp_max_c          ?? null,
        smart_ok:                noeudLive.smart_ok                ?? null,
        smart_reallocated_sectors:noeudLive.smart_reallocated_sectors?? null,
        zfs_arc_hit_rate:        noeudLive.zfs_arc_hit_rate        ?? null,
        zfs_arc_size_gb:         noeudLive.zfs_arc_size_gb         ?? null,
        zfs_available:           noeudLive.zfs_available            ?? null,
        corosync_ok:             noeudLive.corosync_ok              ?? null,
        corosync_quorum_ok:      noeudLive.corosync_quorum_ok       ?? null,
        load_avg_1m:             noeudLive.load_avg_1m              ?? null,
      }])

      setIncidents(a=>[...a,...(data.anomalies||[]).map(an=>({
        ...an,
        timestamp: data.timestamp,
        score:     data.lstm?.score ?? 0.0,
        rapport:   data.rapport,
        incident:  incidentPart,
        vms:       []
      }))])
      addLog('AI Engine',`Incident: ${data.anomalies?.[0]?.message?.slice(0,50)||'anomaly'}`,'#ef4444')
      return
    }
    if (data.type==='reponse') { setChatMessages(m=>[...m,{...data,id:data.id||`rep_${Date.now()}`}]); addLog('AI Assistant','Response generated','#06b6d4'); return }
    if (data.type==='systeme') { setChatMessages(m=>[...m,{...data,id:data.id||`sys_${Date.now()}`}]); return }
    if (data.type==='message_user') { setChatMessages(m=>m.map(msg=>msg.content===data.content&&msg.role==='user'&&!msg.id?{...msg,id:data.id}:msg)); return }
    if (data.type==='message_edite') {
      setChatMessages(m=>{
        const idx=m.findIndex(msg=>msg.id===data.msg_id); if(idx===-1) return m
        const u=[...m]; u[idx]={...u[idx],content:data.content,edited:true}; return u.slice(0,idx+1)
      }); return
    }
    if (data.type==='conversation_created') {
      // Nouvelle conversation — vider les messages immédiatement
      setChatMessages([])
      return
    }
    if (data.type==='conversation_switched') {
      // Charger les messages de la conversation sélectionnée
      const msgs = data.messages || []
      setChatMessages(msgs.map(m=>({...m, type:m.role==='user'?'question':'reponse'})))
      return
    }
    if (data.type==='conversation_deleted') {
      // Charger la nouvelle conversation active (vide si nouvelle)
      const msgs = data.messages || []
      setChatMessages(msgs.map(m=>({...m, type:m.role==='user'?'question':'reponse'})))
      return
    }
    if (data.type==='historique') {
      setChatMessages(data.messages.map(m=>({...m,type:m.role==='user'?'question':'reponse'})))
      return
    }
    if (data.type==='history_cleared') { setChatMessages([]); return }
  }, [addLog])

  useEffect(() => {
    fetch('/api/cluster').then(r=>r.json()).then(d=>{if(!d.error)setCluster(d)}).catch(()=>{})
    const loadRegles=()=>fetch('/api/regles').then(r=>r.json()).then(d=>{if(d.regles&&d.regles.length>0)setReglesDyn(d.regles)}).catch(()=>{})
    loadRegles()
    const t1=setInterval(()=>setUptime(u=>u+1),1000), t3=setInterval(loadRegles,300000)
    addLog('System','OpsPilot initialized','#3b82f6')
    addLog('Connectivity','Cluster connection established','#22c55e')
    return()=>{clearInterval(t1);clearInterval(t3)}
  }, [])
  useEffect(()=>{if(connected)addLog('Network','Real-time stream active','#22c55e')},[connected])

  const sendChat = useCallback((text=chatInput)=>{
    const q=(text||chatInput).trim(); if(!q||thinking) return
    const tmpId=`tmp_${Date.now()}`
    setChatMessages(m=>[...m,{id:tmpId,role:'user',content:q,type:'question',timestamp:new Date().toISOString(),edited:false}])
    send({type:'question',content:q}); setChatInput('')
    addLog('AI Assistant',q.slice(0,60)+(q.length>60?'...':''),'#06b6d4')
  },[chatInput,thinking,send,addLog])

  const editChat       = useCallback((msgId,newContent)=>{ send({type:'edit_message',msg_id:msgId,content:newContent}) },[send])
  const clearChat      = useCallback(()=>{ send({type:'clear_history'}) },[send])

  const alertCount    = cluster?.alertes?.length ?? 0
  const incidentCount = incidents.length
  const isHealthy     = alertCount === 0

  // Noeuds + VMs dynamiques depuis cluster — plus aucun hardcode
  const noeudsLive = cluster?.noeuds || []
  const vmsLive    = cluster?.vms    || []
  const allAssets  = [
    ...noeudsLive.map(n => ({ label: n.nom, type: 'NODE', online: n.statut === 'online' || n.statut === 'Online' })),
    ...vmsLive.map(v    => ({ label: v.nom, type: 'VM',   online: v.statut === 'running' })),
  ]

  const currentPage = PAGES.find(p => p.id === page)
  const C_BLUE      = '#3b82f6'
  const C_YELLOW    = '#f59e0b'
  const C_RED       = '#ef4444'
  const C_GREEN     = '#22c55e'
  const C_MUTED     = '#4a5568'
  const C_SUB       = '#94a3b8'
  const C_BORDER    = '#1e293b'

  return (
    <div style={{ display:'flex', height:'100vh', background:'#050d1a', color:'#e2e8f0', fontFamily:"'Inter','Segoe UI',sans-serif", overflow:'hidden' }}>
      <style>{`
        *{box-sizing:border-box;margin:0;padding:0}
        html,body{-webkit-font-smoothing:antialiased;-moz-osx-font-smoothing:grayscale;text-rendering:optimizeLegibility}
        ::-webkit-scrollbar{width:5px;height:5px}
        ::-webkit-scrollbar-track{background:transparent}
        ::-webkit-scrollbar-thumb{background:#1a2d44;border-radius:3px}
        ::-webkit-scrollbar-thumb:hover{background:#243650}
        @keyframes pulse{0%,100%{opacity:1}50%{opacity:0.3}}
        @keyframes spin{from{transform:rotate(0deg)}to{transform:rotate(360deg)}}
        @keyframes fadeUp{from{opacity:0;transform:translateY(8px)}to{opacity:1;transform:translateY(0)}}
        textarea{resize:none;outline:none}
        button{font-family:inherit;cursor:pointer;border:none;background:none}
        h1,h2,h3{font-weight:800;letter-spacing:-0.02em}
        .nav-btn:hover{background:rgba(59,130,246,0.10) !important;color:#f1f5f9 !important}
        .nav-btn:hover svg{stroke:#3b82f6 !important}
        .nav-btn{transition:all 0.12s}
      `}</style>

      {/* ══════════════════════════ SIDEBAR ══════════════════════════ */}
      <nav style={{ width:236, flexShrink:0, background:'#080f1e', borderRight:`1px solid ${C_BORDER}`, display:'flex', flexDirection:'column' }}>

        {/* ── Logo OpsPilot ── */}
        <div style={{ padding:'18px 16px 16px', borderBottom:`1px solid ${C_BORDER}` }}>
          <div style={{ display:'flex', alignItems:'center', gap:11 }}>
            {/* Logo SVG géométrique */}
            <svg width="34" height="34" viewBox="0 0 34 34" fill="none">
              <rect width="34" height="34" rx="9" fill="#0f2744"/>
              <polygon points="17,5 27,11 27,23 17,29 7,23 7,11" fill="none" stroke="#3b82f6" strokeWidth="1.5"/>
              <polygon points="17,10 22,13 22,21 17,24 12,21 12,13" fill="#1e3a5f"/>
              <line x1="17" y1="13" x2="17" y2="21" stroke="#60a5fa" strokeWidth="1.5" strokeLinecap="round"/>
              <line x1="13" y1="17" x2="21" y2="17" stroke="#60a5fa" strokeWidth="1.5" strokeLinecap="round"/>
              <circle cx="17" cy="17" r="2" fill="#3b82f6"/>
            </svg>
            <div>
              <div style={{
                fontSize: 18,
                fontWeight: 800,
                letterSpacing: '-0.04em',
                background: 'linear-gradient(90deg, #60a5fa 0%, #a78bfa 100%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                lineHeight: 1.1,
              }}>
                OpsPilot
              </div>
              <div style={{ fontSize:10, color:C_MUTED, letterSpacing:'0.14em', fontFamily:'JetBrains Mono, monospace', marginTop:3 }}>
                CLUSTER INTELLIGENCE
              </div>
            </div>
          </div>
        </div>

        {/* ── Navigation ── */}
        <div style={{ flex:1, padding:'8px 8px', display:'flex', flexDirection:'column', gap:1, overflowY:'auto' }}>
          <div style={{ fontSize:10, color:C_MUTED, letterSpacing:'0.13em', fontFamily:'JetBrains Mono, monospace', padding:'10px 8px 5px' }}>
            NAVIGATION
          </div>

          {PAGES.map(({ id, icon, label }) => {
            const badge  = id==='incidents' ? incidentCount : id==='recommendations' ? suggestions.length : 0
            const active = page === id
            return (
              <button
                key={id}
                onClick={() => setPage(id)}
                className="nav-btn"
                style={{
                  display:'flex', alignItems:'center', gap:9, padding:'8px 10px',
                  borderRadius:7,
                  background: active ? 'rgba(59,130,246,0.12)' : 'transparent',
                  border:`1px solid ${active ? 'rgba(59,130,246,0.3)' : 'transparent'}`,
                  color: active ? '#e2e8f0' : C_SUB,
                  fontSize:14, textAlign:'left', transition:'all 0.12s',
                  width:'100%', justifyContent:'space-between',
                }}
              >
                <div style={{ display:'flex', alignItems:'center', gap:9 }}>
                  {Icons[icon]?.(active, C_BLUE)}
                  <span style={{ fontWeight: active ? 600 : 400 }}>{label}</span>
                </div>
                {badge > 0 && (
                  <span style={{
                    background: id==='incidents' ? C_RED : C_YELLOW,
                    color: id==='incidents' ? '#fff' : '#000',
                    borderRadius: 10, padding:'1px 7px',
                    fontSize:10, fontWeight:700,
                    fontFamily:'JetBrains Mono, monospace', flexShrink:0
                  }}>
                    {badge}
                  </span>
                )}
              </button>
            )
          })}

        </div>

        {/* ── Infrastructure Status dynamique ── */}
        <div style={{ padding:'12px 14px 14px', borderTop:`1px solid ${C_BORDER}` }}>
          <div style={{ fontSize:10, color:C_MUTED, letterSpacing:'0.13em', fontFamily:'JetBrains Mono, monospace', marginBottom:9 }}>
            INFRASTRUCTURE STATUS
          </div>

          {/* Skeleton si pas encore chargé */}
          {allAssets.length === 0 ? (
            [0,1,2,3].map(i => (
              <div key={i} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:7 }}>
                <div style={{ width:7, height:7, borderRadius:'50%', background:C_BORDER }}/>
                <div style={{ flex:1, height:8, borderRadius:3, background:C_BORDER, opacity:0.5 }}/>
              </div>
            ))
          ) : (
            allAssets.map(({ label, type, online }) => (
              <div key={label} style={{ display:'flex', alignItems:'center', gap:8, marginBottom:6 }}>
                <div style={{ width:7, height:7, borderRadius:'50%', flexShrink:0, background: online ? C_GREEN : C_RED }}/>
                <span style={{ fontSize:12, color: online ? C_SUB : C_MUTED, fontFamily:'JetBrains Mono, monospace', flex:1, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap' }}>
                  {label}
                </span>
                <span style={{ fontSize:10, color:C_MUTED, fontFamily:'JetBrains Mono, monospace', flexShrink:0 }}>
                  {type}
                </span>
              </div>
            ))
          )}

          {/* Connexion + uptime */}
          <div style={{ marginTop:10, paddingTop:10, borderTop:`1px solid ${C_BORDER}`, display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:11, color:C_MUTED, fontFamily:'JetBrains Mono, monospace' }}>
            <div style={{ display:'flex', alignItems:'center', gap:5 }}>
              <div style={{ width:6, height:6, borderRadius:'50%', background: connected ? C_GREEN : C_YELLOW, animation: connected ? 'none' : 'pulse 1.5s infinite' }}/>
              <span style={{ color: connected ? C_GREEN : C_YELLOW }}>{connected ? 'Live' : 'Reconnecting'}</span>
            </div>
            <span>{fmtTime(uptime)}</span>
          </div>
        </div>
      </nav>

      {/* ══════════════════════ CONTENU PRINCIPAL ══════════════════════ */}
      <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden' }}>

        {/* ── Header ── */}
        <header style={{ height:52, borderBottom:`1px solid ${C_BORDER}`, background:'#080f1e', display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 24px', flexShrink:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            {Icons[currentPage?.icon]?.(true, C_BLUE)}
            <div>
              <span style={{ fontWeight:800, fontSize:16, color:'#f1f5f9' }}>{currentPage?.label}</span>
              <span style={{ fontSize:12, color:C_MUTED, marginLeft:10 }}>{currentPage?.desc}</span>
            </div>
          </div>
          <div style={{ display:'flex', alignItems:'center', gap:14 }}>
            {alertCount > 0 && (
              <button
                onClick={() => setPage('dashboard')}
                style={{ background:'rgba(239,68,68,0.08)', border:`1px solid rgba(239,68,68,0.3)`, borderRadius:6, padding:'4px 12px', color:C_RED, fontSize:11, fontWeight:700, display:'flex', alignItems:'center', gap:6 }}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke={C_RED} strokeWidth="2.5" strokeLinecap="round" strokeLinejoin="round">
                  <path d="M10.29 3.86L1.82 18a2 2 0 001.71 3h16.94a2 2 0 001.71-3L13.71 3.86a2 2 0 00-3.42 0z"/>
                  <line x1="12" y1="9" x2="12" y2="13"/><line x1="12" y1="17" x2="12.01" y2="17"/>
                </svg>
                {alertCount} Active Alert{alertCount > 1 ? 's' : ''}
              </button>
            )}
            <div style={{ padding:'4px 14px', borderRadius:20, fontSize:11, fontWeight:700, background: isHealthy ? 'rgba(34,197,94,0.10)' : 'rgba(239,68,68,0.10)', border:`1px solid ${isHealthy ? 'rgba(34,197,94,0.3)' : 'rgba(239,68,68,0.3)'}`, color: isHealthy ? C_GREEN : C_RED, display:'flex', alignItems:'center', gap:6 }}>
              <div style={{ width:6, height:6, borderRadius:'50%', background: isHealthy ? C_GREEN : C_RED }}/>
              {isHealthy ? 'OPERATIONAL' : 'INCIDENT ACTIVE'}
            </div>
          </div>
        </header>

        {/* ── Pages ── */}
        <main style={{ flex:1, overflow:page==='assistant'?'hidden':'auto', padding:'20px 24px', display:'flex', flexDirection:'column' }}>
          {page==='dashboard'       && <PageDashboard       cluster={cluster} history={history} incidents={incidents} agentLog={agentLog} dernier_lstm={dernierLstm}/>}
          {page==='infrastructure'  && <PageInfrastructure  cluster={cluster} history={history}/>}
          {page==='incidents'       && <PageIncidents       incidents={incidents}/>}
          {page==='rules'           && <PageMonitoringRules reglesDynamiques={reglesDyn}/>}
          {page==='recommendations' && <PageRecommendations suggestions={suggestions}/>}
          {page==='log'             && <PageSystemLog       agentLog={agentLog}/>}
          {page==='assistant'       && <PageAssistant messages={chatMessages} thinking={thinking} input={chatInput} setInput={setChatInput} onSend={sendChat} onEdit={editChat} onClear={clearChat} connected={connected} send={send}/>}
        </main>
      </div>

    </div>
  )
}