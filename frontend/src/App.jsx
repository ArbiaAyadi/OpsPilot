import { useState, useCallback, useEffect, useRef } from 'react'
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
import { PageAuth } from './pages/PageAuth'

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

const NIVEAU_VERS_SEVERITE = { critique:'CRITICAL', important:'HIGH', surveillance:'MONITORING' }
function rapportVersIncident(r) {
  const m = r.nom.match(/report_(\d{8})_(\d{6})_(\w+)\.md/)
  const niveau = m ? m[3] : 'surveillance'
  return {
    anomalies: [],
    timestamp: r.date,
    score:     0,
    rapport:   r.nom,
    structured: {
      severity:  NIVEAU_VERS_SEVERITE[niveau] || 'MONITORING',
      summary:   r.resume || null,
      fix_title: r.titre || null,
      _parse_failed: true,
      _source: 'history',
      _raw: r.resume || 'Historical incident — open the full report below for the complete analysis.',
    },
  }
}

// ← AJOUT : consolide nom + "Change password" + "Sign out" (trois éléments
// séparés dans l'en-tête) en un seul bouton -- le nom lui-même, foncé,
// cliquable -- qui ouvre un petit menu avec les coordonnées du compte et
// les deux actions. Fermeture au clic extérieur (mousedown, pas click --
// se déclenche avant le prochain onClick, évite un clic qui rouvrirait le
// menu juste après l'avoir fermé) ou à la touche Échap.
function UserMenuButton({ user, onChangePassword, onLogout }) {
  const [ouvert, setOuvert] = useState(false)
  const ref = useRef(null)
  // ← Valeurs identiques à celles définies dans App() (ligne ~574) --
  // ce composant est déclaré en dehors de App, ces const locales n'y sont
  // pas visibles (portée de fonction JS, pas une erreur de frappe).
  const C_RED    = '#ef4444'
  const C_MUTED  = '#4a5568'
  const C_SUB    = '#94a3b8'
  const C_BORDER = '#1e293b'

  useEffect(() => {
    if (!ouvert) return
    const fermerSiExterieur = (e) => {
      if (ref.current && !ref.current.contains(e.target)) setOuvert(false)
    }
    const fermerSurEchap = (e) => { if (e.key === 'Escape') setOuvert(false) }
    document.addEventListener('mousedown', fermerSiExterieur)
    document.addEventListener('keydown', fermerSurEchap)
    return () => {
      document.removeEventListener('mousedown', fermerSiExterieur)
      document.removeEventListener('keydown', fermerSurEchap)
    }
  }, [ouvert])

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button
        onClick={() => setOuvert(v => !v)}
        style={{
          display: 'flex', alignItems: 'center', gap: 7,
          background: '#0a1220', border: `1px solid ${C_BORDER}`, borderRadius: 8,
          color: '#e2e8f0', fontSize: 12, fontWeight: 600, padding: '6px 12px',
          cursor: 'pointer', fontFamily: "'Inter', sans-serif",
        }}
      >
        {user?.nom}
        <span style={{ fontSize: 9, color: C_MUTED, transform: ouvert ? 'rotate(180deg)' : 'none', transition: 'transform 0.15s' }}>▼</span>
      </button>

      {ouvert && (
        <div style={{
          position: 'absolute', top: 'calc(100% + 6px)', right: 0, minWidth: 200, zIndex: 50,
          background: '#0a1220', border: `1px solid ${C_BORDER}`, borderRadius: 10,
          boxShadow: '0 8px 24px rgba(0,0,0,0.4)', overflow: 'hidden',
        }}>
          <div style={{ padding: '10px 14px', borderBottom: `1px solid ${C_BORDER}` }}>
            <div style={{ fontSize: 12, fontWeight: 700, color: '#e2e8f0' }}>{user?.nom}</div>
            <div style={{ fontSize: 11, color: C_MUTED, marginTop: 2 }}>{user?.email}</div>
          </div>
          <button
            onClick={() => { setOuvert(false); onChangePassword() }}
            style={{ display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none',
                     color: C_SUB, fontSize: 12, padding: '9px 14px', cursor: 'pointer', fontFamily: "'Inter', sans-serif" }}
            onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(255,255,255,0.04)'}
            onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
          >
            Change password
          </button>
          <button
            onClick={() => { setOuvert(false); onLogout() }}
            style={{ display: 'block', width: '100%', textAlign: 'left', background: 'transparent', border: 'none',
                     borderTop: `1px solid ${C_BORDER}`,
                     color: C_RED, fontSize: 12, padding: '9px 14px', cursor: 'pointer', fontFamily: "'Inter', sans-serif" }}
            onMouseEnter={(e) => e.currentTarget.style.background = 'rgba(239,68,68,0.08)'}
            onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
          >
            Sign out
          </button>
        </div>
      )}
    </div>
  )
}

// ← AJOUT : changement de mot de passe depuis l'app -- le backend
// (/api/auth/change-password) existait déjà, cette modale était la seule
// pièce manquante. Déconnecte automatiquement les autres appareils
// (comportement du backend, pas quelque chose que ce composant décide) --
// voir auth_routes.py pour le raisonnement complet.
function ChangePasswordModal({ onClose }) {
  const [current, setCurrent] = useState('')
  const [nouveau, setNouveau] = useState('')
  const [erreur, setErreur]   = useState(null)
  const [succes, setSucces]   = useState(false)
  const [chargement, setChargement] = useState(false)

  const soumettre = async (e) => {
    e.preventDefault()
    if (chargement) return
    setErreur(null)
    setChargement(true)
    try {
      const r = await fetch('/api/auth/change-password', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_password: current, new_password: nouveau }),
      })
      const data = await r.json().catch(() => ({}))
      if (!r.ok) throw new Error(data.detail || 'Could not change password.')
      setSucces(true)
    } catch (err) {
      setErreur(err.message)
    } finally {
      setChargement(false)
    }
  }

  return (
    <div onClick={onClose} style={{ position:'fixed', inset:0, background:'rgba(0,0,0,0.6)', display:'flex',
                                     alignItems:'center', justifyContent:'center', zIndex:1000 }}>
      <div onClick={e => e.stopPropagation()} style={{ width:360, background:'#0a1220', border:'1px solid #1e293b',
                                                         borderRadius:14, padding:24 }}>
        <div style={{ fontSize:15, fontWeight:700, color:'#e2e8f0', marginBottom:16 }}>Change password</div>
        {succes ? (
          <>
            <div style={{ padding:'9px 12px', borderRadius:7, background:'rgba(34,197,94,0.1)', border:'1px solid rgba(34,197,94,0.3)',
                          color:'#22c55e', fontSize:12, marginBottom:16, lineHeight:1.5 }}>
              Password updated. Other devices have been signed out.
            </div>
            <button onClick={onClose} style={{ width:'100%', padding:'10px 0', borderRadius:8, border:'none',
                                                 background:'#3b82f6', color:'#fff', fontSize:13, fontWeight:700, cursor:'pointer' }}>
              Close
            </button>
          </>
        ) : (
          <form onSubmit={soumettre}>
            {erreur && (
              <div style={{ padding:'9px 12px', borderRadius:7, background:'rgba(239,68,68,0.1)', border:'1px solid rgba(239,68,68,0.3)',
                            color:'#ef4444', fontSize:12, marginBottom:14, lineHeight:1.5 }}>
                {erreur}
              </div>
            )}
            <label style={{ display:'block', fontSize:11, color:'#94a3b8', marginBottom:6, fontFamily:'JetBrains Mono, monospace' }}>CURRENT PASSWORD</label>
            <input type="password" value={current} onChange={e => setCurrent(e.target.value)} required autoFocus
                   style={{ width:'100%', padding:'10px 12px', borderRadius:8, border:'1px solid #1e293b', background:'#070d18',
                            color:'#e2e8f0', fontSize:13, outline:'none', boxSizing:'border-box', marginBottom:14 }} />
            <label style={{ display:'block', fontSize:11, color:'#94a3b8', marginBottom:6, fontFamily:'JetBrains Mono, monospace' }}>NEW PASSWORD</label>
            <input type="password" value={nouveau} onChange={e => setNouveau(e.target.value)} required
                   style={{ width:'100%', padding:'10px 12px', borderRadius:8, border:'1px solid #1e293b', background:'#070d18',
                            color:'#e2e8f0', fontSize:13, outline:'none', boxSizing:'border-box' }} />
            <div style={{ fontSize:10, color:'#4a5568', marginTop:6, marginBottom:16 }}>At least 12 characters.</div>
            <div style={{ display:'flex', gap:8 }}>
              <button type="button" onClick={onClose} style={{ flex:1, padding:'10px 0', borderRadius:8, border:'1px solid #1e293b',
                                                                  background:'transparent', color:'#94a3b8', fontSize:13, cursor:'pointer' }}>
                Cancel
              </button>
              <button type="submit" disabled={chargement} style={{ flex:1, padding:'10px 0', borderRadius:8, border:'none',
                                                                     background:'#3b82f6', color:'#fff', fontSize:13, fontWeight:700,
                                                                     cursor:'pointer', opacity:chargement?0.6:1 }}>
                {chargement ? 'Saving...' : 'Save'}
              </button>
            </div>
          </form>
        )}
      </div>
    </div>
  )
}

export default function App() {
  // ← AJOUT : vérifie la session au montage, avant tout le reste.
  // authChecked distingue "en cours de vérification" de "vérifié, pas
  // connecté" -- sans ça, un court instant sans utilisateur pourrait
  // afficher la page de login puis basculer vers le dashboard, un
  // scintillement visible à chaque chargement.
  const [authChecked, setAuthChecked] = useState(false)
  const [currentUser, setCurrentUser] = useState(null)

  useEffect(() => {
    fetch('/api/auth/me')
      .then(r => r.ok ? r.json() : Promise.reject())
      .then(d => setCurrentUser(d.user))
      .catch(() => setCurrentUser(null))
      .finally(() => setAuthChecked(true))
  }, [])

  const [showChangePwd, setShowChangePwd] = useState(false)

  const handleLogout = useCallback(() => {
    fetch('/api/auth/logout', { method: 'POST' }).finally(() => {
      setCurrentUser(null)
      window.location.reload()  // repart de zero -- coupe la connexion WS, vide tout l'etat en memoire
    })
  }, [])

  // ← CORRIGÉ : ws:// était toujours utilisé, même quand la page est
  // chargée en https:// -- le navigateur bloque alors silencieusement
  // cette connexion non sécurisée (politique de contenu mixte), sans
  // message toujours évident. Construit maintenant le protocole selon
  // celui de la page elle-même : wss:// en HTTPS, ws:// en HTTP --
  // fonctionne dans les deux cas, plus jamais câblé en dur.
  const wsProtocole = window.location.protocol === 'https:' ? 'wss:' : 'ws:'
  const { connected, send, handlerRef } = useWebSocket(`${wsProtocole}//${window.location.host}/ws`)
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
  const [historyTotal,   setHistoryTotal]   = useState(0)
  const [historyOffset,  setHistoryOffset]  = useState(0)
  const [loadingMore,    setLoadingMore]    = useState(false)
  const [dernierLstm,  setDernierLstm]  = useState({ score:0, seuil:0.5, score_if:0, score_lstm:0, lstm_ready:false, drift:false })
  const [reglesDyn,    setReglesDyn]    = useState([])
  const [reglesStale,  setReglesStale]  = useState(false)

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

      const structured   = data.structured || {}

      // ← RETIRÉ : setChatMessages(...) qui ajoutait cette alerte à la
      // conversation du chat. Le chat de l'AI Assistant reste maintenant
      // strictement question/réponse -- l'alerte continue d'alimenter
      // Recommendations (setSuggestions), Incidents (setIncidents) et
      // System Log (addLog juste plus bas), ses trois destinations
      // naturelles, simplement plus mélangée à la conversation.

      const cibleNoeud = structured.target_node || data.anomalies?.[0]?.cible || ''
      const noeudLive  = cluster?.noeuds?.find(n =>
        n.nom?.toLowerCase() === cibleNoeud?.toLowerCase() ||
        cibleNoeud?.toLowerCase().includes(n.nom?.toLowerCase())
      ) || cluster?.noeuds?.[0] || {}

      setSuggestions(s=>[...s, {
        structured,
        // ← AJOUT : id assigné par la base au moment de la sauvegarde
        // (voir surveillance.py) -- permet de marquer cette recommendation
        // résolue plus tard sans ambiguïté, y compris pour celles chargées
        // au démarrage (voir useEffect de chargement initial plus bas).
        recommendation_id: data.recommendation_id ?? null,
        title:       structured.fix_title || data.anomalies?.[0]?.message || 'Infrastructure Issue',
        severity:    structured.severity || data.anomalies?.[0]?.niveau || 'HIGH',
        status:      'OPEN',
        timestamp:   data.timestamp,
        target:      structured.target_node || data.anomalies?.[0]?.cible || 'cluster',
        target_vmid: structured.target_vmid ?? null,
        rapport:     data.rapport,
        cpu_pct:     noeudLive.cpu_pct     ?? null,
        ram_pct:     noeudLive.ram_pct     ?? null,
        disk_pct:    noeudLive.disk_pct    ?? null,
        ram_used_gb: noeudLive.ram_used_gb ?? null,
        ram_total_gb:noeudLive.ram_total_gb?? null,
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
        cpu_temp_max_c:            noeudLive.cpu_temp_max_c            ?? null,
        smart_ok:                  noeudLive.smart_ok                  ?? null,
        smart_reallocated_sectors: noeudLive.smart_reallocated_sectors ?? null,
        zfs_arc_hit_rate:          noeudLive.zfs_arc_hit_rate          ?? null,
        zfs_arc_size_gb:           noeudLive.zfs_arc_size_gb           ?? null,
        zfs_available:             noeudLive.zfs_available             ?? null,
        corosync_ok:               noeudLive.corosync_ok               ?? null,
        corosync_quorum_ok:        noeudLive.corosync_quorum_ok        ?? null,
        load_avg_1m:               noeudLive.load_avg_1m               ?? null,
      }])

      setIncidents(a=>[...a, {
        anomalies: data.anomalies || [],
        timestamp: data.timestamp,
        score:     data.lstm?.score ?? 0.0,
        rapport:   data.rapport,
        structured,
      }])
      // ← AJOUT : chaque incident reçu en direct fait grandir le vrai
      // total d'autant -- sans ça, historyTotal ne reflète que
      // l'instantané pris au montage (ou à la dernière reconnexion) et se
      // périme dès le premier nouvel incident de la journée.
      setHistoryTotal(t => t + 1)
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
      setChatMessages([])
      return
    }
    if (data.type==='conversation_switched') {
      const msgs = data.messages || []
      setChatMessages(msgs.map(m=>({...m, type:m.role==='user'?'question':'reponse'})))
      return
    }
    if (data.type==='conversation_deleted') {
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

  // ← AJOUT : extrait en fonction nommée réutilisable -- appelée au
  // montage ET à chaque reconnexion WebSocket (voir plus bas), pas
  // seulement une fois. Sans ça, historyTotal ne se resynchronise jamais
  // après une coupure réseau et peut dériver silencieusement de la vérité
  // backend au fil des jours -- exactement ce que l'utilisateur a demandé
  // d'éviter ("correct dès maintenant et tous les jours"). Ne dépend que
  // de setters stables (garantis par React), donc [] comme dépendances est
  // correct : cette fonction ne change jamais d'identité.
  const syncHistoryFromBackend = useCallback(() => {
    fetch('/api/rapports?limit=100&offset=0').then(r=>r.json()).then(data=>{
      const rapports = data.rapports || []
      setHistoryTotal(data.total || 0)
      // Ne jamais faire reculer l'offset déjà atteint via "Load more" --
      // ce resync ne doit rafraîchir que le total et rattraper les
      // incidents apparus pendant une éventuelle coupure, jamais annuler
      // une pagination déjà chargée par l'utilisateur.
      setHistoryOffset(o => Math.max(o, rapports.length))
      if (rapports.length === 0) return
      const historique = rapports.map(rapportVersIncident).reverse()
      setIncidents(actuels => {
        const rapportsExistants = new Set(actuels.map(inc => inc.rapport).filter(Boolean))
        const historiqueSansDoublons = historique.filter(h => !rapportsExistants.has(h.rapport))
        return [...historiqueSansDoublons, ...actuels]
      })
    }).catch(()=>{})
  }, [])

  useEffect(() => {
    fetch('/api/cluster').then(r=>r.json()).then(d=>{if(!d.error)setCluster(d)}).catch(()=>{})
    const loadRegles=()=>fetch('/api/regles').then(r=>r.json()).then(d=>{
      if(d.regles&&d.regles.length>0)setReglesDyn(d.regles)
      setReglesStale(!!d.regles_perimees)
    }).catch(()=>{})
    loadRegles()
    syncHistoryFromBackend()
    // ← AJOUT : charge les recommendations encore OUVERTES depuis le
    // backend au démarrage -- avant, "suggestions" ne vivait qu'en mémoire
    // React, perdue à chaque rechargement de page. Ne charge QUE les
    // ouvertes (déjà filtré côté backend, voir /api/recommendations) --
    // volontairement borné, pas un historique qui grossit.
    fetch('/api/recommendations').then(r=>r.json()).then(d=>{
      if (d.recommendations?.length > 0) setSuggestions(s => [...d.recommendations, ...s])
    }).catch(()=>{})
    const t1=setInterval(()=>setUptime(u=>u+1),1000), t3=setInterval(loadRegles,300000)
    addLog('System','OpsPilot initialized','#3b82f6')
    addLog('Connectivity','Cluster connection established','#22c55e')
    return()=>{clearInterval(t1);clearInterval(t3)}
  }, [syncHistoryFromBackend])
  useEffect(()=>{
    if(connected) {
      addLog('Network','Real-time stream active','#22c55e')
      // ← AJOUT : resynchronise historyTotal/historyOffset avec le backend
      // à chaque (re)connexion -- rattrape tout incident manqué et corrige
      // toute dérive du compteur après une coupure réseau, pas seulement
      // au tout premier chargement de la page.
      syncHistoryFromBackend()
    }
  },[connected, syncHistoryFromBackend])
  useEffect(()=>{
    const p = PAGES.find(x=>x.id===page)
    document.title = p ? `OpsPilot — ${p.label}` : 'OpsPilot'
  },[page])

  const loadMoreHistory = useCallback(async () => {
    if (loadingMore) return
    setLoadingMore(true)
    try {
      const r = await fetch(`/api/rapports?limit=100&offset=${historyOffset}`)
      const data = await r.json()
      const rapports = data.rapports || []
      setHistoryTotal(data.total || 0)
      setHistoryOffset(o => o + rapports.length)
      if (rapports.length > 0) {
        const nouveaux = rapports.map(rapportVersIncident).reverse()
        setIncidents(actuels => {
          const rapportsExistants = new Set(actuels.map(inc => inc.rapport).filter(Boolean))
          const sansDoublons = nouveaux.filter(h => !rapportsExistants.has(h.rapport))
          return [...sansDoublons, ...actuels]
        })
      }
    } catch { /* silencieux -- le bouton reste disponible pour réessayer */ }
    finally { setLoadingMore(false) }
  }, [historyOffset, loadingMore])

  // ← AJOUT : marque une recommendation résolue -- déclenché soit par un
  // clic explicite sur la carte, soit automatiquement quand une action
  // proposée s'exécute avec succès (voir PageRecommendations.jsx). Retire
  // du state local immédiatement (pas d'attente de la réponse serveur) --
  // cohérent avec "seulement les OPEN sont affichées", et évite un délai
  // visible pour une action déjà confirmée côté UI (le bouton n'affiche
  // "success" qu'après la vraie exécution).
  const resolveRecommendation = useCallback((recId) => {
    if (!recId) return
    setSuggestions(s => s.filter(sug => sug.recommendation_id !== recId))
    fetch(`/api/recommendations/${recId}/resolve`, { method: 'POST' }).catch(()=>{})
  }, [])

  // ← AJOUT : suppression définitive d'une recommendation ACTIVE --
  // distincte de resolveRecommendation ci-dessus (qui garde une trace
  // dans l'historique). Delete ne laisse rien derrière, pour un faux
  // positif ou du bruit. Retire du state local immédiatement, même
  // principe que resolveRecommendation.
  const deleteRecommendation = useCallback((recId) => {
    if (!recId) return
    setSuggestions(s => s.filter(sug => sug.recommendation_id !== recId))
    fetch(`/api/recommendations/${recId}`, { method: 'DELETE' }).catch(()=>{})
  }, [])

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
  // ← CORRIGÉ : le badge doit refléter le vrai total backend, pas
  // seulement ce qui est chargé en mémoire côté frontend -- avec 801
  // incidents réels et seulement 100 chargés au démarrage, le badge
  // affichait 100 (visible dans la capture d'écran). historyTotal est
  // maintenant tenu à jour en continu : synchronisé au montage ET à
  // chaque reconnexion WebSocket (syncHistoryFromBackend), incrémenté à
  // chaque nouvel incident reçu en direct (voir handlerRef, bloc
  // "alerte"). Math.max() protège uniquement la toute première fraction
  // de seconde avant que /api/rapports n'ait répondu (historyTotal encore
  // à 0 à ce moment-là).
  const incidentCount = Math.max(incidents.length, historyTotal)
  const isHealthy     = alertCount === 0

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
  const C_BORDER    = '#1c2f4a'

  // ← AJOUT : bloque l'accès à tout le reste tant que la session n'est
  // pas vérifiée, ou l'affiche la page de connexion si elle est absente.
  // Placé APRÈS toutes les déclarations de hooks (useState/useEffect/
  // useCallback ci-dessus) -- jamais avant, un retour anticipé entre des
  // hooks casserait les règles de React (nombre de hooks doit rester
  // identique à chaque rendu).
  if (!authChecked) {
    return (
      <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:'100vh', background:'#050a14', color:'#4a5568', fontSize:13, fontFamily:'JetBrains Mono, monospace' }}>
        Loading...
      </div>
    )
  }
  if (!currentUser) {
    return <PageAuth onAuthenticated={setCurrentUser} />
  }

  return (
    <div style={{
      display:'flex', height:'100vh', color:'#e2e8f0',
      fontFamily:"'Inter','Segoe UI',sans-serif", overflow:'hidden',
      // ← MODIFIÉ : aplat '#050d1a' -> dégradé bleu en profondeur.
      // Trois couches superposées, uniquement en CSS (aucune image, donc
      // rien à charger) :
      //  1. une lueur bleue diffuse en haut à gauche, qui donne l'impression
      //     d'une source de lumière plutôt qu'un fond plat ;
      //  2. une seconde lueur, cyan et plus discrète, en bas à droite,
      //     pour éviter que le dégradé paraisse unidirectionnel ;
      //  3. un dégradé linéaire de base, du bleu nuit vers le presque-noir.
      // C'est ce qui donne à la référence sa profondeur : la lumière ne
      // vient pas de partout à la fois.
      background:
        'radial-gradient(1100px 700px at 12% -8%, rgba(37,99,235,0.16) 0%, transparent 62%),' +
        'radial-gradient(900px 600px at 108% 108%, rgba(34,211,238,0.10) 0%, transparent 58%),' +
        'linear-gradient(168deg, #071427 0%, #050c18 46%, #03070f 100%)',
      backgroundAttachment: 'fixed',
    }}>
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
        .nav-btn:hover{background:rgba(34,211,238,0.10) !important;color:#f1f5f9 !important}
        .nav-btn:hover svg{stroke:#22d3ee !important}
        .nav-btn{transition:all 0.12s}
        /* ← AJOUT : trame géométrique très discrète sur le fond, comme les
           lignes diagonales à peine visibles de la référence. Opacité
           volontairement minime -- elle doit se deviner, jamais se
           remarquer. pointer-events:none pour qu'elle n'intercepte aucun
           clic. */
        .fond-trame{
          position:fixed; inset:0; pointer-events:none; z-index:0;
          background-image:
            linear-gradient(115deg, transparent 0%, transparent 49.6%, rgba(59,130,246,0.055) 49.8%, transparent 50%),
            linear-gradient(65deg,  transparent 0%, transparent 49.6%, rgba(34,211,238,0.045) 49.8%, transparent 50%);
          background-size: 340px 340px, 470px 470px;
        }
        /* Panneaux légèrement translucides : le dégradé du fond transparaît
           à travers la barre latérale et l'en-tête, au lieu de les couper
           par deux aplats opaques. */
        .panneau-verre{
          background: rgba(9,18,32,0.72) !important;
          backdrop-filter: blur(14px);
          -webkit-backdrop-filter: blur(14px);
        }
      `}</style>

      <div className="fond-trame"/>

      <nav className="panneau-verre" style={{ width:236, flexShrink:0, borderRight:`1px solid ${C_BORDER}`, display:'flex', flexDirection:'column', position:'relative', zIndex:1 }}>

        <div style={{ padding:'18px 16px 16px', borderBottom:`1px solid ${C_BORDER}` }}>
          <div style={{ display:'flex', alignItems:'center', gap:11 }}>
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
                // ← MODIFIÉ : 18 -> 23px. Le nom du produit est l'élément
                // le plus important de la barre latérale ; à 18px il pesait
                // moins lourd que les libellés de navigation juste en
                // dessous, ce qui inversait la hiérarchie.
                fontSize: 23,
                fontWeight: 800,
                letterSpacing: '-0.035em',
                // ← MODIFIÉ : le dégradé finissait sur du violet (#a78bfa),
                // une couleur qui n'existe nulle part ailleurs dans
                // l'interface. Remplacé par un vrai dégradé de BLEUS :
                // bleu ciel pâle -> bleu franc, en diagonale (105deg
                // plutôt que 90deg, pour que la transition suive le regard
                // plutôt que de couper le mot horizontalement).
                background: 'linear-gradient(105deg, #bae6fd 0%, #60a5fa 46%, #3b82f6 100%)',
                WebkitBackgroundClip: 'text',
                WebkitTextFillColor: 'transparent',
                lineHeight: 1.05,
              }}>
                OpsPilot
              </div>
              {/* ← MODIFIÉ : marginTop 3 -> 7 (le sous-titre était collé au
                  nom, les deux se lisaient comme un seul bloc), et couleur
                  #4a5568 -> #3c5169, plus foncée. Un sur-titre de ce type
                  doit se deviner, pas concurrencer le nom du produit --
                  reste au-dessus du seuil de lisibilité sur ce fond. */}
              <div style={{ fontSize:9.5, color:'#3c5169', letterSpacing:'0.16em', fontFamily:'JetBrains Mono, monospace', marginTop:7 }}>
                CLUSTER INTELLIGENCE
              </div>
            </div>
          </div>
        </div>

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

        <div style={{ padding:'12px 14px 14px', borderTop:`1px solid ${C_BORDER}` }}>
          <div style={{ fontSize:10, color:C_MUTED, letterSpacing:'0.13em', fontFamily:'JetBrains Mono, monospace', marginBottom:9 }}>
            INFRASTRUCTURE STATUS
          </div>

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

          <div style={{ marginTop:10, paddingTop:10, borderTop:`1px solid ${C_BORDER}`, display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:11, color:C_MUTED, fontFamily:'JetBrains Mono, monospace' }}>
            <div style={{ display:'flex', alignItems:'center', gap:5 }}>
              <div style={{ width:6, height:6, borderRadius:'50%', background: connected ? C_GREEN : C_YELLOW, animation: connected ? 'none' : 'pulse 1.5s infinite' }}/>
              <span style={{ color: connected ? C_GREEN : C_YELLOW }}>{connected ? 'Live' : 'Reconnecting'}</span>
            </div>
            <span>{fmtTime(uptime)}</span>
          </div>
        </div>
      </nav>

      <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', position:'relative', zIndex:1 }}>

        <header className="panneau-verre" style={{ height:52, borderBottom:`1px solid ${C_BORDER}`, display:'flex', alignItems:'center', justifyContent:'space-between', padding:'0 24px', flexShrink:0 }}>
          <div style={{ display:'flex', alignItems:'center', gap:10 }}>
            {Icons[currentPage?.icon]?.(true, C_BLUE)}
            <div>
              {/* ← MODIFIÉ : blanc plat -> dégradé bleu clair/cyan, comme
                  les titres de page eux-mêmes. Un blanc pur tranchait
                  durement sur le nouveau fond bleu ; le dégradé s'y pose
                  naturellement tout en restant parfaitement lisible. */}
              {/* ← MODIFIÉ (3e passe) : ni blanc pur (dur), ni bleu (se
                  confond avec le fond), ni blanc chaud -- un dégradé
                  VERTICAL du blanc vers un gris-bleu très pâle. Le texte
                  garde la lisibilité d'un blanc franc en haut et gagne
                  une légère profondeur vers le bas, technique courante
                  sur les interfaces produit soignées où un aplat blanc
                  paraît toujours un peu brut. */}
              <span style={{ fontWeight:800, fontSize:16, letterSpacing:'-0.01em',
                             background:'linear-gradient(180deg, #ffffff 0%, #b9c9dd 130%)',
                             WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent' }}>{currentPage?.label}</span>
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
            {/* ← MODIFIÉ : nom + Change password + Sign out consolidés en un
                seul bouton foncé (le nom lui-même) qui ouvre un menu --
                remplace les trois éléments séparés précédents. */}
            <div style={{ paddingLeft: 14, borderLeft: `1px solid ${C_BORDER}` }}>
              <UserMenuButton user={currentUser} onChangePassword={() => setShowChangePwd(true)} onLogout={handleLogout} />
            </div>
          </div>
        </header>

        <main style={{ flex:1, overflow:page==='assistant'?'hidden':'auto', padding:'20px 24px', display:'flex', flexDirection:'column' }}>
          {page==='dashboard'       && <PageDashboard       cluster={cluster} history={history} incidents={incidents} agentLog={agentLog} dernier_lstm={dernierLstm}/>}
          {page==='infrastructure'  && <PageInfrastructure  cluster={cluster} history={history}/>}
          {page==='incidents'       && <PageIncidents       incidents={incidents} historyTotal={historyTotal} historyOffset={historyOffset} loadingMore={loadingMore} onLoadMore={loadMoreHistory}/>}
          {page==='rules'           && <PageMonitoringRules reglesDynamiques={reglesDyn} stale={reglesStale}/>}
          {page==='recommendations' && <PageRecommendations suggestions={suggestions} vms={vmsLive} hasHistory={incidents.length > 0} onResolve={resolveRecommendation} onDelete={deleteRecommendation}/>}
          {page==='log'             && <PageSystemLog       agentLog={agentLog}/>}
          {page==='assistant'       && <PageAssistant messages={chatMessages} thinking={thinking} input={chatInput} setInput={setChatInput} onSend={sendChat} onEdit={editChat} onClear={clearChat} connected={connected} send={send}/>}
        </main>
      </div>

      {showChangePwd && <ChangePasswordModal onClose={() => setShowChangePwd(false)} />}
    </div>
  )
}