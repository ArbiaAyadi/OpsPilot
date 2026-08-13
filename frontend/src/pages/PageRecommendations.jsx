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
import { useState } from 'react'
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
function ActionButton({ actionId, params, risk }) {
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

// ── Une étape du plan généré par le LLM ──────────────────────────────────────
function StepRow({ step }) {
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
        <ActionButton actionId={step.action_id} params={step.action_params} risk={step.risk || 'medium'} />
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

function RecoCard({ s, vms }) {
  const [expanded, setExpanded] = useState(true)
  const sev         = normalizeSeverity(s.severity)
  const sevColor    = severityColor(s.severity)
  const structured  = s.structured || {}
  const parseFailed = !!structured._parse_failed

  return (
    <Card glow={sevColor} style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex' }}>
        <div style={{ width: 4, background: sevColor, flexShrink: 0, borderRadius: '8px 0 0 8px' }} />

        <div style={{ flex: 1, padding: '16px 20px' }}>
          {/* Header */}
          <div
            onClick={() => setExpanded(v => !v)}
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', cursor: 'pointer', marginBottom: expanded ? 14 : 0 }}
          >
            <div style={{ flex: 1, marginRight: 12 }}>
              <div style={{ fontSize: 15, fontWeight: 700, color: C.text, lineHeight: 1.3 }}>{s.title}</div>
              <div style={{ fontSize: 11, color: C.muted, marginTop: 2, fontFamily: 'JetBrains Mono, monospace' }}>
                {s.target}{s.target_vmid != null ? ` · ${nomVm(s.target_vmid, vms)}` : ''}
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
              <Chip label={sev} color={sevColor} />
              <Chip label={s.status || 'OPEN'} color={s.status === 'RESOLVED' ? C.green : C.yellow} />
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
              {(s.swap_pct !== null || s.cpu_iowait_pct !== null || s.cpu_temp_max_c !== null || s.zfs_available || s.corosync_ok !== null) && (
                <div style={{ marginBottom: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  {s.swap_pct !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>SWAP</div>
                      <div style={{ fontSize: 12, color: s.swap_pct > 50 ? C.red : s.swap_pct > 20 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.swap_pct}% {s.swap_used_gb !== null ? `(${s.swap_used_gb}GB)` : ''}
                      </div>
                    </div>
                  )}
                  {s.cpu_iowait_pct !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>I/O WAIT</div>
                      <div style={{ fontSize: 12, color: s.cpu_iowait_pct > 30 ? C.red : s.cpu_iowait_pct > 10 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.cpu_iowait_pct}%
                      </div>
                    </div>
                  )}
                  {s.disk_read_latency_ms !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>DISK LATENCY</div>
                      <div style={{ fontSize: 12, color: s.disk_read_latency_ms > 20 ? C.red : s.disk_read_latency_ms > 10 ? C.orange : C.green, fontWeight: 600 }}>
                        R:{s.disk_read_latency_ms}ms W:{s.disk_write_latency_ms}ms
                      </div>
                    </div>
                  )}
                  {(s.net_errors_in !== null && (s.net_errors_in > 0 || s.net_errors_out > 0)) && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>NET ERRORS</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        in:{s.net_errors_in} out:{s.net_errors_out}
                      </div>
                    </div>
                  )}
                  {s.cpu_temp_max_c !== null && s.cpu_temp_max_c > 0 && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>CPU TEMP</div>
                      <div style={{ fontSize: 12, color: s.cpu_temp_max_c > 85 ? C.red : s.cpu_temp_max_c > 75 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.cpu_temp_max_c}°C
                      </div>
                    </div>
                  )}
                  {s.smart_ok !== null && !s.smart_ok && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>SMART</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        FAIL — {s.smart_reallocated_sectors} bad sectors
                      </div>
                    </div>
                  )}
                  {s.zfs_available && s.zfs_arc_hit_rate !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>ZFS ARC</div>
                      <div style={{ fontSize: 12, color: s.zfs_arc_hit_rate < 70 ? C.red : s.zfs_arc_hit_rate < 85 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.zfs_arc_hit_rate}% hit · {s.zfs_arc_size_gb}GB
                      </div>
                    </div>
                  )}
                  {s.corosync_ok !== null && !s.corosync_ok && (
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
                  informées par les métriques et la recherche Tavily/wiki ── */}
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
                  structured.steps.map((step, i) => <StepRow key={i} step={step} />)
                )}
              </div>

              {/* ← RÉTABLI : lien docs retiré par inadvertance avec l'ancienne
                  bibliothèque SOLUTIONS (qui le portait). Basé sur
                  structured.doc_url -- calculé côté backend via
                  get_doc_url(dominant) (web_search.py), jamais par le LLM,
                  donc jamais une URL hallucinée. */}
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

export function PageRecommendations({ suggestions, vms = [], hasHistory = false }) {
  const critCount = suggestions.filter(s => normalizeSeverity(s.severity) === 'CRITICAL').length
  const highCount = suggestions.filter(s => normalizeSeverity(s.severity) === 'HIGH').length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 800, color: C.text, marginBottom: 6 }}>Remediation Actions</h2>
          <div style={{ fontSize: 13, color: C.sub }}>
            AI-generated analysis and step-by-step plan per incident, grounded in live infrastructure metrics
            and real-time{' '}
            <a href="https://pve.proxmox.com/pve-docs/pve-admin-guide.html" target="_blank" rel="noopener noreferrer" style={{ color: C.blue, textDecoration: 'none' }}>
              Proxmox VE documentation
            </a>
          </div>
        </div>
        {suggestions.length > 0 && (
          <div style={{ display: 'flex', gap: 8 }}>
            {critCount > 0 && <Chip label={`${critCount} CRITICAL`} color={C.red} />}
            {highCount > 0 && <Chip label={`${highCount} HIGH`}     color={C.orange} />}
            <Chip label={`${suggestions.length} total`} color={C.muted} />
          </div>
        )}
      </div>

      {/* Empty state */}
      {suggestions.length === 0 ? (
        <Card style={{ padding: '60px 0', textAlign: 'center' }}>
          <div style={{ fontSize: 38, marginBottom: 14 }}>◈</div>
          <div style={{ fontSize: 15, color: C.sub, marginBottom: 6 }}>
            {hasHistory ? 'Nothing active right now' : 'No active recommendations'}
          </div>
          <div style={{ fontSize: 12, color: C.muted }}>
            {hasHistory
              ? 'No live remediation needed at the moment — see the Incidents page for past activity.'
              : 'All resources are within Proxmox VE official thresholds'}
          </div>
        </Card>
      ) : (
        suggestions.map((s, i) => <RecoCard key={i} s={s} vms={vms} />)
      )}
    </div>
  )
}