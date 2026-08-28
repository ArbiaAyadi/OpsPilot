import React from 'react'
/**
 * OpsPilot — Page Recommendations
 *
 * ← REFONTE : affiche maintenant le JSON structuré RÉELLEMENT généré par le
 * LLM pour chaque incident (s.structured, construit par
 * incident_prompt.construire_prompt_specifique + parser_reponse_llm, transmis
 * via surveillance.py -> App.jsx). Plus de bibliothèque SOLUTIONS figée par
 * type de problème, plus de parsing par regex d'un texte libre -- causes,
 * étapes (immediate/short_term/long_term) et cible (node/vmid) viennent
 * directement du LLM, informé par les vraies métriques de toute
 * l'infrastructure et la vraie recherche Tavily/wiki Proxmox.
 *
 * Sécurité inchangée : un step ne montre un bouton "Accepter & Exécuter" QUE
 * si son action_id a déjà été validé côté backend (incident_prompt._valider_step)
 * contre action_executor.ACTION_META -- jamais une confiance aveugle dans le
 * texte libre du LLM. Un step sans action_id validé reste un texte
 * informatif, sans bouton.
 */
import { useState, useEffect } from 'react'
import { C } from '../utils/colors'
import { severityColor, normalizeSeverity } from '../styles/theme'
import { Card } from '../components/Card'
import { Chip } from '../components/Common'

function CopyButton({ text }) {
  const [copied, setCopied] = useState(false)
  return (
    <button
      onClick={() => { navigator.clipboard.writeText(text); setCopied(true); setTimeout(() => setCopied(false), 2000) }}
      style={{ background: 'none', border: `1px solid ${C.border}`, borderRadius: 4, color: copied ? C.green : C.muted, fontSize: 10, padding: '2px 9px', cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace', transition: 'color 0.2s', flexShrink: 0 }}
    >
      {copied ? '✓ copied' : 'copy'}
    </button>
  )
}

// ── Human-in-the-Loop : Boutons Accept/Reject ──────────────────────────────
// Câblé sur action_id + action_params déjà validés côté backend (voir
// incident_prompt._valider_step) -- jamais sur le texte libre du LLM
// directement. Inchangé dans son fonctionnement : le LLM ne déclenche
// jamais rien lui-même, il ne fait que proposer, l'utilisateur accepte,
// l'agent exécute via /api/actions/execute.
function ActionButton({ actionId, params, risk, onSuccess }) {
  const [status, setStatus] = React.useState('idle')
  const [message, setMessage] = React.useState('')
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
      // ← CORRIGÉ : onSuccess() (qui fait disparaître toute la carte
      // parente) était appelé DANS LA MÊME FONCTION, juste après
      // setStatus('success') -- React n'avait pas le temps de peindre le
      // message de confirmation vert avant que le parent ne retire la
      // carte entière. Résultat concret : cliquer "Accepter & Exécuter"
      // faisait disparaître toute la recommendation sans jamais voir la
      // confirmation -- exactement ce qui ressemble à "la recommendation
      // disparaît" sans raison apparente. Délai de 2s : assez pour que le
      // message soit lu, assez court pour ne pas sembler bloqué.
      if (data.ok && onSuccess) setTimeout(onSuccess, 2000)
    } catch (e) {
      setStatus('error')
      setMessage('Erreur réseau')
    }
  }

  if (status === 'success') return (
    <div style={{ padding:'6px 12px', borderRadius:6, background:'#22c55e15',
                  border:'1px solid #22c55e30', fontSize:11, color:C.green, marginTop:6 }}>
      {message}
    </div>
  )
  if (status === 'error') return (
    <div style={{ padding:'6px 12px', borderRadius:6, background:'#ef444415',
                  border:'1px solid #ef444430', fontSize:11, color:C.red, marginTop:6 }}>
      ✗ {message}
    </div>
  )
  if (status === 'rejected') return (
    <div style={{ fontSize:10, color:C.muted, marginTop:4, fontStyle:'italic' }}>
      Refusé — aucune modification effectuée
    </div>
  )

  return (
    <div style={{ display:'flex', alignItems:'center', gap:8, marginTop:6 }}>
      <span style={{ fontSize:10, color:riskColor, fontWeight:600 }}>
        {risk === 'low' ? '🟢 Risque faible' : risk === 'medium' ? '🟠 Risque moyen' : '🔴 Risque élevé'}
      </span>
      <button onClick={execute} disabled={status === 'loading' || !params?.node}
        style={{ padding:'4px 12px', borderRadius:6, border:'none', background:C.green,
                 color:'#fff', cursor:'pointer', fontSize:11, fontWeight:700,
                 opacity: (status === 'loading' || !params?.node) ? 0.5 : 1, transition:'all 0.15s' }}>
        {status === 'loading' ? '⟳ Exécution...' : '✓ Accepter & Exécuter'}
      </button>
      <button onClick={() => setStatus('rejected')} disabled={status === 'loading'}
        style={{ padding:'4px 10px', borderRadius:6, border:`1px solid ${C.border}`,
                 background:'transparent', color:C.muted, cursor:'pointer', fontSize:11 }}>
        ✗ Refuser
      </button>
    </div>
  )
}

const PHASE_STYLE = {
  immediate:  { label: 'IMMEDIATE',  color: C.red    },
  short_term: { label: 'SHORT TERM', color: C.orange },
  long_term:  { label: 'LONG TERM',  color: C.blue   },
}

// ── Suppression définitive -- distincte de Resolve, jamais confondue.
// Clic une fois arme le bouton (3s pour confirmer), un second clic dans ce
// délai supprime réellement -- pas de popup modal, juste un état visuel
// clair. Réutilisé sur les cartes actives ET l'historique.
// ← AJOUT : même composant que PageIncidents.jsx -- remplace window.confirm()
// qui affichait la boîte native du navigateur, mal assortie au thème sombre.
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

function DeleteButton({ onConfirm }) {
  const [armed, setArmed]       = useState(false)
  const [deleting, setDeleting] = useState(false)

  useEffect(() => {
    if (!armed) return
    const t = setTimeout(() => setArmed(false), 3000)
    return () => clearTimeout(t)
  }, [armed])

  const handleClick = (e) => {
    e.stopPropagation()
    if (deleting) return
    if (!armed) { setArmed(true); return }
    setDeleting(true)
    onConfirm()
  }

  return (
    <button onClick={handleClick} disabled={deleting} title={armed ? 'Cliquer à nouveau pour confirmer' : 'Supprimer définitivement'}
      style={{ background: armed ? C.red + '20' : 'transparent', border: `1px solid ${armed ? C.red : C.border}`,
               borderRadius: 6, color: deleting ? C.muted : (armed ? C.red : C.sub), fontSize: 10, padding: '3px 9px',
               cursor: deleting ? 'default' : 'pointer', fontFamily: 'JetBrains Mono, monospace',
               opacity: deleting ? 0.5 : 1, transition: 'all 0.15s' }}>
      {deleting ? '⟳' : armed ? 'Confirm?' : '🗑 Delete'}
    </button>
  )
}

// ── Une étape du plan généré par le LLM ──────────────────────────────────────
function StepRow({ step, onActionSuccess }) {
  const phase = PHASE_STYLE[step.phase] || { label: (step.phase || '').toUpperCase(), color: C.muted }
  return (
    <div style={{ padding: '10px 12px', borderRadius: 8, background: C.surface, border: `1px solid ${C.border}`, marginBottom: 8 }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: step.command ? 8 : 0 }}>
        <span style={{ fontSize: 9, fontWeight: 700, color: phase.color, background: phase.color + '18',
                       border: `1px solid ${phase.color}40`, borderRadius: 8, padding: '1px 7px',
                       fontFamily: 'JetBrains Mono, monospace', flexShrink: 0 }}>
          {phase.label}
        </span>
        <span style={{ fontSize: 13, color: C.sub, lineHeight: 1.5, flex: 1 }}>{step.action}</span>
        {step.action_id && (
          <span style={{ fontSize:9, color:C.muted, background:C.card, padding:'2px 6px', borderRadius:4,
                         flexShrink:0, fontFamily:'JetBrains Mono,monospace' }}>
            AUTO
          </span>
        )}
      </div>
      {step.command && (
        <div style={{ background: '#070d18', borderRadius: 7, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '5px 10px', borderBottom: `1px solid ${C.border}`, background: '#0a1220' }}>
            <span style={{ fontSize: 10, color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>bash</span>
            <CopyButton text={step.command} />
          </div>
          <pre style={{ margin: 0, padding: '9px 12px', fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: '#7dd3fc', whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>
            {step.command}
          </pre>
        </div>
      )}
      {/* ← Bouton "Accepter & Exécuter" UNIQUEMENT si action_id a survécu à
          la validation backend (incident_prompt._valider_step) -- sinon,
          cette étape reste un texte informatif, jamais un bouton qui
          échouerait silencieusement à l'exécution. */}
      {step.action_id && step.action_params && (
        <ActionButton actionId={step.action_id} params={step.action_params} risk={step.risk || 'medium'} onSuccess={onActionSuccess} />
      )}
    </div>
  )
}

// Résout un VMID vers un nom lisible, si connu -- purement pour l'affichage,
// n'affecte jamais les paramètres envoyés à /api/actions/execute (ceux-là
// viennent déjà complets et validés depuis le backend).
function nomVm(vmid, vms) {
  const vm = vms?.find(v => String(v.vmid) === String(vmid))
  return vm ? (vm.nom || vm.name || `VM ${vmid}`) : `VM ${vmid}`
}

// ← RETIRÉ : HistoryRecoCard (vue minimale, lecture seule) -- remplacé par
// RecoCard({resolved: true}) ci-dessous, qui affiche désormais la même
// analyse complète ET le même plan d'action (boutons Accepter & Exécuter
// inclus) dans les deux onglets, sur demande explicite. Seule différence
// restante entre Active et History : le bouton "Resolve" n'a pas de sens
// sur un incident déjà résolu, donc masqué uniquement là.

// ── Repli si le JSON du LLM n'a pas pu être parsé ────────────────────────────
// Rare (voir incident_prompt.parser_reponse_llm), mais possible. Montre le
// texte brut plutôt qu'un contenu figé qui prétendrait être l'analyse de
// l'IA alors que ça n'en serait pas une.
function RawFallback({ raw }) {
  return (
    <div style={{ padding: '10px 14px', background: C.bg, borderRadius: 7, border: `1px solid ${C.border}` }}>
      <div style={{ fontSize: 10, color: C.orange, fontFamily: 'JetBrains Mono, monospace', marginBottom: 6 }}>
        ⚠ AI response could not be parsed as structured data — showing raw output
      </div>
      <div style={{ fontSize: 12, color: C.sub, whiteSpace: 'pre-wrap', lineHeight: 1.6 }}>{raw}</div>
    </div>
  )
}

function RecoCard({ s, vms, resolved = false, onResolve, onDelete, selectionMode = false, selected = false, onToggleSelect }) {
  // ← MODIFIÉ : par défaut repliée en historique (potentiellement beaucoup
  // d'entrées, ne pas toutes les déployer d'un coup), dépliée par défaut
  // en Active (comportement inchangé).
  // ← MODIFIÉ : repliée par défaut dans les deux cas maintenant (avant :
  // dépliée par défaut côté Active) -- même comportement qu'Incidents,
  // où rien ne s'ouvre tant qu'on ne clique pas explicitement dessus.
  const [expanded, setExpanded] = useState(false)
  const [resolving, setResolving] = useState(false)
  const sev         = normalizeSeverity(s.severity)
  const sevColor    = severityColor(s.severity)
  const structured  = s.structured || {}
  const parseFailed = !!structured._parse_failed
  const quandResolu = s.resolu_at
    ? new Date(s.resolu_at).toLocaleString('fr-FR', { day: '2-digit', month: 'short', hour: '2-digit', minute: '2-digit' })
    : null

  const resolve = () => {
    if (resolving) return
    setResolving(true)
    onResolve?.(s.recommendation_id)
  }

  return (
    <Card glow={sevColor} style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex' }}>
        <div style={{ width: 4, background: resolved ? C.green : sevColor, flexShrink: 0, borderRadius: '8px 0 0 8px' }} />

        {selectionMode && (
          <div style={{ display: 'flex', alignItems: 'flex-start', paddingTop: 18, paddingLeft: 14 }}
               onClick={(e) => { e.stopPropagation(); onToggleSelect?.(s.recommendation_id) }}>
            <input type="checkbox" checked={selected} readOnly
                   style={{ width: 16, height: 16, cursor: 'pointer', accentColor: C.blue }} />
          </div>
        )}

        <div style={{ flex: 1, padding: '16px 20px' }}>
          {/* Header */}
          <div
            onClick={() => selectionMode ? onToggleSelect?.(s.recommendation_id) : setExpanded(v => !v)}
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', cursor: 'pointer', marginBottom: expanded ? 14 : 0 }}
          >
            <div style={{ flex: 1, marginRight: 12 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: C.text, lineHeight: 1.3 }}>{s.title}</div>
              <div style={{ fontSize: 11, color: C.muted, marginTop: 2, fontFamily: 'JetBrains Mono, monospace' }}>
                {s.target}{s.target_vmid != null ? ` · ${nomVm(s.target_vmid, vms)}` : ''}
                {/* ← AJOUT : date et heure, même format que la page
                    Incidents (timestamp.slice(0,19).replace('T',' ')) --
                    déjà présent dans les données (posé par surveillance.py
                    à la création), jamais affiché ici jusque-là. Permet de
                    faire le lien visuellement entre un incident et sa
                    recommendation en comparant simplement les heures des
                    deux pages, sans lien explicite entre elles. */}
                {s.timestamp ? ` · ${s.timestamp.slice(0, 19).replace('T', ' ')}` : ''}
                {resolved && quandResolu ? ` · resolved ${quandResolu}` : ''}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
              <span onClick={(e) => e.stopPropagation()}>
                <Chip label={sev} color={sevColor} />
              </span>
              {/* ← MODIFIÉ : le bouton Resolve n'apparaît que côté Active --
                  resolve() sur un incident déjà résolu n'a pas de sens. Le
                  plan d'action (Accepter & Exécuter) et Delete, eux, restent
                  disponibles dans les deux onglets. */}
              {!resolved && (
                <button
                  onClick={(e) => { e.stopPropagation(); resolve() }}
                  disabled={resolving}
                  title="Marquer comme résolu"
                  style={{ background: 'transparent', border: `1px solid ${C.border}`, borderRadius: 6,
                           color: resolving ? C.muted : C.sub, fontSize: 10, padding: '3px 9px',
                           cursor: resolving ? 'default' : 'pointer', fontFamily: 'JetBrains Mono, monospace',
                           opacity: resolving ? 0.5 : 1 }}>
                  {resolving ? '⟳' : '✓ Resolve'}
                </button>
              )}
              <DeleteButton onConfirm={() => onDelete?.(s.recommendation_id)} />
              <span style={{ color: C.muted, fontSize: 11, marginLeft: 4 }}>{expanded ? '▲' : '▼'}</span>
            </div>
          </div>

          {expanded && (parseFailed ? (
            <RawFallback raw={structured._raw || s.title} />
          ) : (
            <>
              {/* ── AI ANALYSIS — généré par le LLM pour cet incident précis ── */}
              <div style={{ marginBottom: 14, padding: '10px 14px', background: sevColor + '14', borderRadius: 7, border: `1px solid ${sevColor}35` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 5 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: sevColor, letterSpacing: '0.08em', fontFamily: 'JetBrains Mono, monospace' }}>
                    AI ANALYSIS
                  </span>
                  <span style={{ fontSize: 9, fontWeight: 700, color: C.blue, background: C.blue + '18', border: `1px solid ${C.blue}40`, borderRadius: 8, padding: '1px 7px', fontFamily: 'JetBrains Mono, monospace' }}>
                    AI-GENERATED
                  </span>
                </div>
                <div style={{ fontSize: 12, color: C.text, lineHeight: 1.5 }}>{structured.summary}</div>
                {(structured.causes || []).length > 0 && (
                  <ul style={{ margin: '8px 0 0', paddingLeft: 18 }}>
                    {structured.causes.map((c, i) => (
                      <li key={i} style={{ fontSize: 11, color: C.sub, lineHeight: 1.6 }}>{c}</li>
                    ))}
                  </ul>
                )}
              </div>

              {/* Métriques complètes niveaux 1+2+3 si disponibles -- lecture
                  directe des métriques live du nœud (App.jsx), indépendant
                  du format de sortie du LLM. */}
              {(s.swap_pct != null || s.cpu_iowait_pct != null || s.cpu_temp_max_c != null || s.zfs_available || s.corosync_ok != null) && (
                <div style={{ marginBottom: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  {s.swap_pct != null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>SWAP</div>
                      <div style={{ fontSize: 12, color: s.swap_pct > 50 ? C.red : s.swap_pct > 20 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.swap_pct}% {s.swap_used_gb != null ? `(${s.swap_used_gb}GB)` : ''}
                      </div>
                    </div>
                  )}
                  {s.cpu_iowait_pct != null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>I/O WAIT</div>
                      <div style={{ fontSize: 12, color: s.cpu_iowait_pct > 30 ? C.red : s.cpu_iowait_pct > 10 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.cpu_iowait_pct}%
                      </div>
                    </div>
                  )}
                  {s.disk_read_latency_ms != null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>DISK LATENCY</div>
                      <div style={{ fontSize: 12, color: s.disk_read_latency_ms > 20 ? C.red : s.disk_read_latency_ms > 10 ? C.orange : C.green, fontWeight: 600 }}>
                        R:{s.disk_read_latency_ms}ms W:{s.disk_write_latency_ms}ms
                      </div>
                    </div>
                  )}
                  {(s.net_errors_in != null && (s.net_errors_in > 0 || s.net_errors_out > 0)) && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>NET ERRORS</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        in:{s.net_errors_in} out:{s.net_errors_out}
                      </div>
                    </div>
                  )}
                  {s.cpu_temp_max_c != null && s.cpu_temp_max_c > 0 && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>CPU TEMP</div>
                      <div style={{ fontSize: 12, color: s.cpu_temp_max_c > 85 ? C.red : s.cpu_temp_max_c > 75 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.cpu_temp_max_c}°C
                      </div>
                    </div>
                  )}
                  {s.smart_ok != null && !s.smart_ok && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>SMART</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        FAIL — {s.smart_reallocated_sectors} bad sectors
                      </div>
                    </div>
                  )}
                  {s.zfs_available && s.zfs_arc_hit_rate != null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>ZFS ARC</div>
                      <div style={{ fontSize: 12, color: s.zfs_arc_hit_rate < 70 ? C.red : s.zfs_arc_hit_rate < 85 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.zfs_arc_hit_rate}% hit · {s.zfs_arc_size_gb}GB
                      </div>
                    </div>
                  )}
                  {s.corosync_ok != null && !s.corosync_ok && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>COROSYNC</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        DEGRADED — quorum {s.corosync_quorum_ok ? 'OK' : 'LOST'}
                      </div>
                    </div>
                  )}
                </div>
              )}

              {/* ── Avertissement de sécurité, si le LLM en a émis un ── */}
              {structured.warning && (
                <div style={{ marginBottom: 14, padding: '10px 14px', background: C.red + '10', borderRadius: 7, border: `1px solid ${C.red}35`, display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                  <span style={{ fontSize: 14, color: C.red, flexShrink: 0 }}>⚠</span>
                  <span style={{ fontSize: 12, color: C.red, fontWeight: 600, lineHeight: 1.5 }}>{structured.warning}</span>
                </div>
              )}

              {/* ── AI RECOMMENDED PLAN — étapes réellement générées par le LLM,
                  informées par les métriques et la recherche Tavily/wiki.
                  Boutons Accepter & Exécuter actifs ici aussi bien en Active
                  qu'en History, sur demande explicite -- utile pour rejouer
                  une action si le même problème resurgit plus tard. ── */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 8, marginBottom: 8 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: C.sub, letterSpacing: '0.08em', fontFamily: 'JetBrains Mono, monospace' }}>
                    AI RECOMMENDED PLAN
                  </span>
                  <span style={{ fontSize: 9, fontWeight: 700, color: C.blue, background: C.blue + '18', border: `1px solid ${C.blue}40`, borderRadius: 8, padding: '1px 7px', fontFamily: 'JetBrains Mono, monospace' }}>
                    AI-GENERATED
                  </span>
                </div>
                {(structured.steps || []).length === 0 ? (
                  <div style={{ fontSize: 12, color: C.muted, fontStyle: 'italic' }}>No steps returned.</div>
                ) : (
                  structured.steps.map((step, i) => <StepRow key={i} step={step} onActionSuccess={resolved ? undefined : resolve} />)
                )}
              </div>

              {structured.doc_url && (
                <div style={{ textAlign: 'right' }}>
                  <a href={structured.doc_url} target="_blank" rel="noopener noreferrer"
                    style={{ fontSize: 9, color: C.blue, textDecoration: 'none', fontFamily: 'JetBrains Mono, monospace', opacity: 0.7 }}>
                    📖 docs
                  </a>
                </div>
              )}
            </>
          ))}
        </div>
      </div>
    </Card>
  )
}

// ── Onglet Historique -- paginé, chargé à la demande (pas au montage de
// la page, seulement quand l'onglet est ouvert) pour ne jamais alourdir
// le chargement initial avec un historique qui grossit avec le temps.
const HISTORY_PAGE_SIZE = 20

function TabButton({ active, onClick, children }) {
  return (
    <button onClick={onClick}
      style={{ padding: '7px 16px', borderRadius: 8, border: `1px solid ${active ? C.blue : C.border}`,
               background: active ? C.blue + '18' : 'transparent', color: active ? C.blue : C.sub,
               fontSize: 12, fontWeight: 700, cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace',
               transition: 'all 0.15s' }}>
      {children}
    </button>
  )
}

export function PageRecommendations({ suggestions, vms = [], hasHistory = false, onResolve, onDelete }) {
  const [tab, setTab] = useState('active')  // 'active' | 'history'
  const [historyItems, setHistoryItems]   = useState([])
  const [historyTotal, setHistoryTotal]   = useState(0)
  const [historyOffset, setHistoryOffset] = useState(0)
  const [loadingHistory, setLoadingHistory] = useState(false)
  const [historyLoadedOnce, setHistoryLoadedOnce] = useState(false)

  // ← AJOUT : sélection multiple -- réinitialisée au changement d'onglet,
  // pour ne jamais laisser croire qu'une sélection faite dans Active
  // s'applique aussi à History (deux listes distinctes, deux contextes
  // de suppression distincts -- voir deleteActive/deleteFromHistory).
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedIds, setSelectedIds]     = useState(() => new Set())
  // ← AJOUT : chargement automatique de toutes les pages restantes avant
  // une sélection totale réelle (History est paginé) + confirmation
  // stylée à la place de window.confirm().
  const [attenteChargementPourTout, setAttenteChargementPourTout] = useState(false)
  const [confirmationEnCours, setConfirmationEnCours]             = useState(false)

  const changerOnglet = (nouvelOnglet) => {
    setTab(nouvelOnglet)
    setSelectionMode(false)
    setSelectedIds(new Set())
  }

  // ← AJOUT : découpage temporel demandé explicitement -- Active = créé
  // aujourd'hui, History = plus ancien, QUEL QUE SOIT le statut résolu/
  // ouvert. Avant, "Active" regroupait TOUT ce qui n'était pas résolu,
  // même vieux de plusieurs jours -- un problème jamais formellement
  // résolu s'y accumulait indéfiniment (130+ observées). Même principe
  // que Today/History sur la page Incidents, pour la cohérence demandée
  // entre les deux pages.
  const estAujourdhui = (timestamp) => {
    if (!timestamp) return false
    const d = new Date(timestamp), n = new Date()
    return d.getFullYear() === n.getFullYear() && d.getMonth() === n.getMonth() && d.getDate() === n.getDate()
  }
  const suggestionsAujourdhui = suggestions.filter(s => estAujourdhui(s.timestamp))
  // ← les anciennes MAIS toujours ouvertes (jamais résolues) -- rejoignent
  // maintenant l'affichage de History, tout en restant de VRAIS éléments
  // "actifs" côté données (deleteActive/onResolve continuent de s'y
  // appliquer, pas deleteFromHistory -- voir le rendu plus bas).
  const suggestionsAnciennesOuvertes = suggestions.filter(s => !estAujourdhui(s.timestamp))

  const toggleSelect = (recId) => {
    if (recId == null) return
    setSelectedIds(prev => {
      const suivant = new Set(prev)
      if (suivant.has(recId)) suivant.delete(recId)
      else suivant.add(recId)
      return suivant
    })
  }

  // ← MODIFIÉ : History est paginé (loadHistory par lots de
  // HISTORY_PAGE_SIZE) -- avant, "Select all" ne sélectionnait que ce qui
  // était déjà chargé (ex: 113 sur 863 réels côté serveur), sans qu'aucun
  // signe n'indique que ce n'était qu'un sous-ensemble. L'effet ci-dessous
  // charge automatiquement le reste avant de sélectionner pour de vrai.
  // ← MODIFIÉ : "Active" ne compte plus que suggestionsAujourdhui ;
  // "History" compte maintenant le mélange (anciennes ouvertes + vrai
  // historique résolu) -- cohérent avec le découpage temporel ci-dessus.
  const idsVisibles = (tab === 'active' ? suggestionsAujourdhui : [...suggestionsAnciennesOuvertes, ...historyItems])
    .map(s => s.recommendation_id).filter(id => id != null)
  const toutSelectionne = idsVisibles.length > 0 && idsVisibles.every(id => selectedIds.has(id))

  // ← DÉPLACÉ : loadHistory doit être déclarée avant l'effet ci-dessous
  // qui l'utilise (une const n'est pas accessible avant sa ligne de
  // déclaration en JS) -- inchangée sinon, simplement à un autre endroit
  // du fichier.
  const loadHistory = (offset) => {
    setLoadingHistory(true)
    fetch(`/api/recommendations/history?limit=${HISTORY_PAGE_SIZE}&offset=${offset}`)
      .then(r => r.json())
      .then(d => {
        setHistoryItems(items => offset === 0 ? (d.recommendations || []) : [...items, ...(d.recommendations || [])])
        setHistoryTotal(d.total || 0)
        setHistoryOffset(offset)
        setHistoryLoadedOnce(true)
      })
      .catch(() => {})
      .finally(() => setLoadingHistory(false))
  }

  useEffect(() => {
    if (!attenteChargementPourTout) return
    if (loadingHistory) return
    if (tab === 'history' && historyItems.length < historyTotal) {
      loadHistory(historyOffset + HISTORY_PAGE_SIZE)
    } else {
      setAttenteChargementPourTout(false)
      setSelectedIds(new Set(idsVisibles))
    }
  }, [attenteChargementPourTout, loadingHistory, historyItems.length, historyTotal, tab])

  const selectionnerTout = () => {
    if (toutSelectionne) { setSelectedIds(new Set()); return }
    if (tab === 'history' && historyItems.length < historyTotal) {
      setAttenteChargementPourTout(true)
    } else {
      setSelectedIds(new Set(idsVisibles))
    }
  }

  const critCount = suggestions.filter(s => normalizeSeverity(s.severity) === 'CRITICAL').length
  const highCount = suggestions.filter(s => normalizeSeverity(s.severity) === 'HIGH').length

  // Charge l'historique la première fois qu'on ouvre cet onglet -- pas au
  // montage de la page, l'onglet Actif reste l'affichage par défaut.
  const openHistoryTab = () => {
    changerOnglet('history')
    if (!historyLoadedOnce) loadHistory(0)
  }

  // ← AJOUT : delete sur une carte ACTIVE doit aussi la retirer de
  // suggestions (état du parent App.jsx) -- delete sur une carte
  // d'HISTORIQUE ne touche que l'état local de cet onglet. Même
  // /api/recommendations/{id} DELETE dans les deux cas, seul l'endroit où
  // l'état local est mis à jour diffère.
  // ← CORRIGÉ : "!recId" traitait un id valant 0 comme absent (falsy JS) --
  // improbable avec PostgreSQL (les séquences SERIAL démarrent à 1), mais
  // trouvé en testant la suppression groupée et corrigé tant qu'à faire.
  // "recId == null" ne rejette que null/undefined, jamais 0.
  const deleteActive = (recId) => {
    if (recId == null) return
    onDelete?.(recId)
  }
  const deleteFromHistory = (recId) => {
    if (recId == null) return
    setHistoryItems(items => items.filter(h => h.recommendation_id !== recId))
    setHistoryTotal(t => Math.max(0, t - 1))
    fetch(`/api/recommendations/${recId}`, { method: 'DELETE' }).catch(() => {})
  }

  // ← AJOUT : suppression groupée -- réutilise exactement deleteActive/
  // deleteFromHistory pour chaque id sélectionné (une seule logique de
  // suppression, jamais dupliquée). window.confirm ici plutôt que le
  // pattern "armer puis confirmer" des cartes individuelles -- une action
  // groupée et potentiellement large mérite une confirmation plus
  // explicite qu'un simple second clic.
  // ← MODIFIÉ : window.confirm() retiré (boîte native non stylée) -- ouvre
  // maintenant la modale stylée (ConfirmModal), suppression réelle dans
  // confirmerSuppression. Réutilise toujours exactement deleteActive/
  // deleteFromHistory pour chaque id sélectionné (une seule logique de
  // suppression, jamais dupliquée).
  const deleteSelection = () => {
    if (selectedIds.size === 0) return
    setConfirmationEnCours(true)
  }

  // ← CORRIGÉ : avant, TOUS les ids sélectionnés étaient supprimés de
  // l'affichage immédiatement, puis tous les appels réseau partaient EN
  // MÊME TEMPS sans aucune limite -- pour des centaines d'éléments, ça
  // sature le serveur et une partie échoue silencieusement (.catch(()
  // => {})) tout en étant déjà affichée comme supprimée. Voir
  // PageIncidents.jsx pour le même correctif, raisonnement complet.
  // History : suivi de succès complet, comme Incidents (le fetch vit
  // directement dans deleteFromHistory ci-dessus). Active : deleteActive
  // délègue le fetch réel à App.jsx (onDelete), hors de portée directe
  // ici -- au minimum, les appels sont espacés par lots pour ne jamais
  // en déclencher des centaines d'un coup, même sans pouvoir suivre leur
  // succès individuel depuis ce fichier.
  // ← RENFORCÉ (après un vrai retour terrain : 8 en parallèle restait
  // trop pour un unique processus Uvicorn qui fait AUSSI tourner le
  // cycle de surveillance en tâche de fond -- des échecs constatés en
  // pratique malgré le premier correctif). Voir PageIncidents.jsx pour
  // le même renforcement, raisonnement complet. Lots réduits de 8 à 4,
  // pause entre chaque lot. History : vraies tentatives automatiques (3
  // essais, délai croissant) avant de compter un échec, comme Incidents.
  // Active : deleteActive ne renvoie aucune indication de succès (fetch
  // géré dans App.jsx, hors de portée ici) -- on retente quand même
  // l'appel plusieurs fois par sécurité, sans risque : un DELETE sur un
  // id déjà supprimé est un no-op côté serveur, jamais une double
  // suppression problématique.
  const [suppressionEnCours, setSuppressionEnCours] = useState(false)
  const [progressionSuppression, setProgressionSuppression] = useState({ fait: 0, total: 0 })

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

  const confirmerSuppression = async () => {
    setConfirmationEnCours(false)
    const aTraiter = [...selectedIds]
    setSuppressionEnCours(true)
    setProgressionSuppression({ fait: 0, total: aTraiter.length })

    const TAILLE_LOT = 4
    if (tab === 'active') {
      for (let i = 0; i < aTraiter.length; i += TAILLE_LOT) {
        const lot = aTraiter.slice(i, i + TAILLE_LOT)
        // Sécurité par répétition (pas un vrai suivi de succès, voir
        // commentaire ci-dessus) -- 2 appels espacés par id.
        lot.forEach(id => { deleteActive(id); setTimeout(() => deleteActive(id), 400) })
        setProgressionSuppression({ fait: Math.min(i + TAILLE_LOT, aTraiter.length), total: aTraiter.length })
        if (i + TAILLE_LOT < aTraiter.length) await new Promise(r => setTimeout(r, 250))
      }
    } else {
      // History : fetch géré directement ici (deleteFromHistory) --
      // suivi de succès complet possible.
      let reussisTotal = []
      for (let i = 0; i < aTraiter.length; i += TAILLE_LOT) {
        const lot = aTraiter.slice(i, i + TAILLE_LOT)
        const resultats = await Promise.allSettled(
          lot.map(async id => ({ id, ok: await supprimerAvecRetry(`/api/recommendations/${id}`) }))
        )
        const reussisCeLot = resultats
          .filter(r => r.status === 'fulfilled' && r.value.ok)
          .map(r => r.value.id)
        reussisTotal = [...reussisTotal, ...reussisCeLot]
        setProgressionSuppression({ fait: Math.min(i + TAILLE_LOT, aTraiter.length), total: aTraiter.length })
        // ← mise à jour progressive, lot par lot -- seuls les succès
        // confirmés jusqu'ici disparaissent, jamais tout d'un coup en
        // supposant que ça va marcher.
        setHistoryItems(items => items.filter(h => !reussisCeLot.includes(h.recommendation_id)))
        setHistoryTotal(t => Math.max(0, t - reussisCeLot.length))
        if (i + TAILLE_LOT < aTraiter.length) await new Promise(r => setTimeout(r, 150))
      }
      const echecs = aTraiter.length - reussisTotal.length
      if (echecs > 0) {
        alert(`${echecs} sur ${aTraiter.length} suppressions ont échoué -- réessaie de les sélectionner et supprimer à nouveau (le serveur était peut-être temporairement surchargé).`)
      }
    }

    setSuppressionEnCours(false)
    setSelectedIds(new Set())
    setSelectionMode(false)
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div style={{ display: 'flex', gap: 12, alignItems: 'flex-start' }}>
          {/* ← RETIRÉ : pastille dégradée bleu/cyan devant le titre. */}
          <div>
            <h2 style={{ fontSize:22, fontWeight:800, marginBottom:6, letterSpacing:'-0.02em',
                         background:'linear-gradient(180deg, #ffffff 0%, #b9c9dd 130%)',
                         WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent' }}>Remediation Actions</h2>
            <div style={{ fontSize: 13, color: C.sub }}>
              AI-generated analysis and step-by-step plan per incident, grounded in live infrastructure metrics
              and real-time{' '}
              <a href="https://pve.proxmox.com/pve-docs/pve-admin-guide.html" target="_blank" rel="noopener noreferrer" style={{ color: C.blue, textDecoration: 'none' }}>
                Proxmox VE documentation
              </a>
            </div>
          </div>
        </div>
        {tab === 'active' && suggestions.length > 0 && (
          <div style={{ display: 'flex', gap: 8 }}>
            {critCount > 0 && <Chip label={`${critCount} CRITICAL`} color={C.red} />}
            {highCount > 0 && <Chip label={`${highCount} HIGH`}     color={C.orange} />}
            <Chip label={`${suggestions.length} total`} color={C.muted} />
          </div>
        )}
      </div>

      {/* Onglets -- même principe déjà appliqué à Incidents (Today/History) */}
      <div style={{ display: 'flex', gap: 8, alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', gap: 8 }}>
          <TabButton active={tab === 'active'} onClick={() => changerOnglet('active')}>
            Active {suggestions.length > 0 ? `(${suggestions.length})` : ''}
          </TabButton>
          <TabButton active={tab === 'history'} onClick={openHistoryTab}>
            History {historyLoadedOnce ? `(${historyTotal})` : ''}
          </TabButton>
        </div>

        {/* ← AJOUT : sélection multiple -- un bouton pour entrer/sortir du
            mode, une barre d'action qui n'apparaît qu'une fois au moins un
            élément coché. Visible seulement s'il y a quelque chose à
            sélectionner dans l'onglet courant. */}
        {((tab === 'active' && suggestions.length > 0) || (tab === 'history' && historyItems.length > 0)) && (
          <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
            {selectionMode && (
              <button onClick={selectionnerTout} disabled={attenteChargementPourTout}
                style={{ background: 'transparent', border: `1px solid ${C.border}`, borderRadius: 6,
                         color: C.sub, fontSize: 11, padding: '5px 12px', cursor: attenteChargementPourTout ? 'default' : 'pointer',
                         opacity: attenteChargementPourTout ? 0.6 : 1,
                         fontFamily: 'JetBrains Mono, monospace' }}>
                {attenteChargementPourTout ? '⟳ Loading all…' : toutSelectionne ? 'Deselect all' : 'Select all'}
              </button>
            )}
            {suppressionEnCours && (
              <span style={{ fontSize: 12, color: C.orange, fontFamily: 'JetBrains Mono, monospace' }}>
                ⟳ Deleting {progressionSuppression.fait}/{progressionSuppression.total}…
              </span>
            )}
            {selectionMode && selectedIds.size > 0 && !suppressionEnCours && (
              <>
                <span style={{ fontSize: 12, color: C.sub, fontFamily: 'JetBrains Mono, monospace' }}>
                  {selectedIds.size} selected
                </span>
                <button onClick={deleteSelection}
                  style={{ background: 'transparent', border: `1px solid ${C.red}60`, borderRadius: 6,
                           color: C.red, fontSize: 11, padding: '5px 12px', cursor: 'pointer',
                           fontFamily: 'JetBrains Mono, monospace' }}>
                  🗑 Delete selected
                </button>
              </>
            )}
            <button
              onClick={() => { setSelectionMode(v => !v); setSelectedIds(new Set()) }}
              style={{ background: selectionMode ? 'transparent' : C.blue+'12',
                       border: `1px solid ${selectionMode ? C.border : C.blue+'50'}`, borderRadius: 6,
                       color: selectionMode ? C.sub : C.blue, fontSize: 11, padding: '5px 12px', cursor: 'pointer',
                       fontFamily: 'JetBrains Mono, monospace', transition: 'all 0.15s' }}>
              {selectionMode ? 'Cancel' : 'Select'}
            </button>
          </div>
        )}
      </div>

      {tab === 'active' ? (
        suggestions.length === 0 ? (
          <Card style={{ padding: '60px 0', textAlign: 'center' }}>
            <div style={{ fontSize: 38, marginBottom: 14 }}>◈</div>
            <div style={{ fontSize: 15, color: C.sub, marginBottom: 6 }}>
              {hasHistory ? 'Nothing active right now' : 'No active recommendations'}
            </div>
            <div style={{ fontSize: 12, color: C.muted }}>
              {hasHistory
                ? 'No live remediation needed at the moment — see the History tab for past activity.'
                : 'All resources are within Proxmox VE official thresholds'}
            </div>
          </Card>
        ) : (
          // ← MODIFIÉ : plus récent en premier -- suggestions grandit par
          // ajout à la fin côté App.jsx (chaque nouvelle alerte WebSocket
          // vient s'ajouter après les précédentes), donc inversé
          // localement ici pour l'affichage. Même logique que la page
          // Incidents (reversed = [...incidents].reverse()).
          [...suggestions].reverse().map((s, i) => (
            <RecoCard key={s.recommendation_id ?? `idx-${i}`} s={s} vms={vms} onResolve={onResolve} onDelete={deleteActive}
                      selectionMode={selectionMode} selected={selectedIds.has(s.recommendation_id)} onToggleSelect={toggleSelect} />
          ))
        )
      ) : (
        <>
          {historyItems.length === 0 && !loadingHistory ? (
            <Card style={{ padding: '60px 0', textAlign: 'center' }}>
              <div style={{ fontSize: 38, marginBottom: 14 }}>◈</div>
              <div style={{ fontSize: 15, color: C.sub, marginBottom: 6 }}>No resolved recommendations yet</div>
              <div style={{ fontSize: 12, color: C.muted }}>Resolved items will show up here, most recent first.</div>
            </Card>
          ) : (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
              {/* ← AJOUT : regroupement par date (Today/Yesterday/date
                  précise) -- même logique que PageIncidents.jsx, sur
                  demande explicite de cohérence visuelle entre les deux
                  pages. historyItems déjà trié du plus récent au plus
                  ancien (resolu_at DESC côté backend), donc les groupes
                  sortent naturellement dans le bon ordre. */}
              {Object.entries(
                historyItems.reduce((groupes, s) => {
                  const d = s.resolu_at ? new Date(s.resolu_at) : null
                  const aujourdhui = new Date()
                  const hier = new Date(); hier.setDate(hier.getDate() - 1)
                  let cle = 'Unknown date'
                  if (d) {
                    if (d.getFullYear()===aujourdhui.getFullYear() && d.getMonth()===aujourdhui.getMonth() && d.getDate()===aujourdhui.getDate()) cle = 'Today'
                    else if (d.getFullYear()===hier.getFullYear() && d.getMonth()===hier.getMonth() && d.getDate()===hier.getDate()) cle = 'Yesterday'
                    else cle = d.toLocaleDateString('en-US', { weekday: 'short', day: '2-digit', month: 'short', year: 'numeric' })
                  }
                  ;(groupes[cle] = groupes[cle] || []).push(s)
                  return groupes
                }, {})
              ).map(([dateLabel, items]) => (
                <div key={dateLabel}>
                  <div style={{ fontSize: 10, fontWeight: 700, color: C.muted, letterSpacing: '0.1em', fontFamily: 'JetBrains Mono, monospace', margin: '10px 0 6px', paddingLeft: 2 }}>
                    {dateLabel.toUpperCase()} · {items.length}
                  </div>
                  <div style={{ display: 'flex', flexDirection: 'column', gap: 10 }}>
                    {items.map((s, i) => (
                      <RecoCard key={s.recommendation_id ?? `hidx-${i}`} s={s} vms={vms} resolved={s.status === 'RESOLVED'} onDelete={deleteFromHistory}
                                selectionMode={selectionMode} selected={selectedIds.has(s.recommendation_id)} onToggleSelect={toggleSelect} />
                    ))}
                  </div>
                </div>
              ))}
            </div>
          )}
          {historyItems.length < historyTotal && (
            <button onClick={() => loadHistory(historyOffset + HISTORY_PAGE_SIZE)} disabled={loadingHistory}
              style={{ alignSelf: 'center', padding: '8px 20px', borderRadius: 8, border: `1px solid ${C.border}`,
                       background: 'transparent', color: C.sub, fontSize: 12, cursor: loadingHistory ? 'default' : 'pointer',
                       fontFamily: 'JetBrains Mono, monospace', opacity: loadingHistory ? 0.5 : 1 }}>
              {loadingHistory ? 'Loading...' : `Load more (${historyTotal - historyItems.length} remaining)`}
            </button>
          )}
        </>
      )}

      {confirmationEnCours && (
        <ConfirmModal
          message={`Delete ${selectedIds.size} selected item${selectedIds.size > 1 ? 's' : ''}? This cannot be undone.`}
          onConfirm={confirmerSuppression}
          onCancel={() => setConfirmationEnCours(false)}
        />
      )}
    </div>
  )
}