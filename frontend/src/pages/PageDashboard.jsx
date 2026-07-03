import { useState } from 'react'
import {
  AreaChart, Area, XAxis, YAxis, CartesianGrid, Tooltip,
  ResponsiveContainer, ReferenceLine
} from 'recharts'
import { C } from '../utils/colors'
import { riskColor, severityColor, normalizeSeverity } from '../styles/theme'
import { fmtUptime } from '../utils/formatters'
import { Card } from '../components/Card'
import { OnlineDot } from '../components/OnlineDot'
import { SectionLabel, Chip, Placeholder } from '../components/Common'
import { StatusBadge } from '../components/StatusBadge'

const ChartTip = ({ active, payload }) => {
  if (!active || !payload?.length) return null
  return (
    <div style={{ background: C.card, border: `1px solid ${C.borderHi}`, borderRadius: 6, padding: '8px 12px', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
      {payload.map((p, i) => (
        <div key={i} style={{ color: p.color, display: 'flex', gap: 12, justifyContent: 'space-between' }}>
          <span>{p.name}</span>
          <span style={{ fontWeight: 700 }}>{p.value?.toFixed(1)}%</span>
        </div>
      ))}
    </div>
  )
}

function MiniBar({ value = 0, warn = 75, crit = 90 }) {
  const color = value >= crit ? C.red : value >= warn ? C.orange : C.green
  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
      <div style={{ width: 60, height: 3, background: C.border, borderRadius: 2 }}>
        <div style={{ width: `${Math.min(100, value)}%`, height: '100%', background: color, borderRadius: 2, transition: 'width 0.7s ease' }}/>
      </div>
      <span style={{ fontSize: 12, color, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, minWidth: 40 }}>{value?.toFixed(1)}%</span>
    </div>
  )
}

function NodeSummaryCard({ n }) {
  const isOnline = n.statut === 'online'
  const overall = !isOnline ? 'OFFLINE'
    : n.cpu_pct > 80 || n.ram_pct > 85 || n.disk_pct > 90 ? 'CRITICAL'
    : n.cpu_pct > 65 || n.ram_pct > 75 || n.disk_pct > 80 ? 'WARNING'
    : 'OK'
  const overallColor = { OK: C.green, WARNING: C.orange, CRITICAL: C.red, OFFLINE: C.red }[overall]

  return (
    <Card glow={overall !== 'OK' && overall !== 'OFFLINE' ? overallColor : undefined}
      style={{ padding: '14px 18px', border: `1px solid ${overall === 'OFFLINE' ? C.red+'30' : C.border}` }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', marginBottom: 12 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
          <OnlineDot online={isOnline}/>
          <div>
            <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>{n.nom}</div>
            <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>
              {n.cpu_cores || '?'} cores · {n.ram_total_gb || 0} GB RAM
              {n.uptime_h > 0 && ` · up ${fmtUptime(n.uptime_h)}`}
            </div>
          </div>
        </div>
        <StatusBadge label={overall} color={overallColor}/>
      </div>
      {!isOnline ? (
        <div style={{ fontSize: 11, color: C.red, fontStyle: 'italic' }}>
          Unreachable — check network connectivity
        </div>
      ) : (
        <div style={{ display: 'flex', flexDirection: 'column', gap: 7 }}>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: C.sub }}>
            <span>CPU</span><MiniBar value={n.cpu_pct} warn={65} crit={80}/>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: C.sub }}>
            <span>RAM</span><MiniBar value={n.ram_pct} warn={75} crit={85}/>
          </div>
          <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', fontSize: 12, color: C.sub }}>
            <span>Disk</span><MiniBar value={n.disk_pct} warn={80} crit={90}/>
          </div>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginTop: 3 }}>
            {(n.swap_pct || 0) > 20 && (
              <span style={{ fontSize: 10, color: (n.swap_pct||0) > 50 ? C.orange : C.muted }}>
                Swap {(n.swap_pct||0).toFixed(0)}%
              </span>
            )}
            {(n.cpu_iowait_pct || 0) > 5 && (
              <span style={{ fontSize: 10, color: (n.cpu_iowait_pct||0) > 15 ? C.orange : C.muted }}>
                I/O {(n.cpu_iowait_pct||0).toFixed(0)}%
              </span>
            )}
            {(n.cpu_temp_max_c || 0) > 0 && (
              <span style={{ fontSize: 10, color: (n.cpu_temp_max_c||0) > 75 ? C.orange : C.muted }}>
                {(n.cpu_temp_max_c||0).toFixed(0)}°C
              </span>
            )}
            {n.smart_ok === false && (
              <span style={{ fontSize: 10, color: C.red, fontWeight: 700 }}>⚠ SMART FAIL</span>
            )}
            {!n.corosync_ok && (
              <span style={{ fontSize: 10, color: C.red, fontWeight: 700 }}>⚠ QUORUM</span>
            )}
          </div>
        </div>
      )}
    </Card>
  )
}

// agentLog retiré des props — Recent Activity supprimé (disponible dans Audit Log)
export function PageDashboard({
  cluster, history, incidents,
  dernier_lstm = { score: 0, score_if: 0, score_lstm: 0, lstm_ready: false, seuil: 0.5, drift: false }
}) {
  const [chartScope, setChartScope] = useState('cluster')

  if (!cluster) return <Placeholder icon="◈" text="Connexion au cluster en cours..."/>

  const { noeuds = [], vms = [], alertes = [] } = cluster
  const noeudsActifs = noeuds.filter(n => n.statut === 'online')
  const avgCPU = noeudsActifs.length ? noeudsActifs.reduce((s, n) => s + n.cpu_pct, 0) / noeudsActifs.length : 0
  const avgRAM = noeudsActifs.length ? noeudsActifs.reduce((s, n) => s + n.ram_pct, 0) / noeudsActifs.length : 0
  const health = alertes.some(a => a.niveau === 'CRITIQUE') ? 'CRITICAL'
    : alertes.length > 0 ? 'DEGRADED' : 'OPERATIONAL'
  const healthColor = { OPERATIONAL: C.green, DEGRADED: C.yellow, CRITICAL: C.red }[health]

  const vmsRunning = vms.filter(v => v.statut === 'running' || v.statut === 'en cours')
  const vmsStopped = vms.filter(v => v.statut !== 'running' && v.statut !== 'en cours')
  const critAlertes = alertes.filter(a => a.niveau === 'CRITIQUE' || a.niveau === 'CRITICAL')
  const clusterUptimeH = noeudsActifs.length ? Math.max(...noeudsActifs.map(n => n.uptime_h || 0)) : 0
  const corosyncOk = noeuds.every(n => n.corosync_ok !== false)
  const quorumOk   = noeuds.every(n => n.corosync_quorum_ok !== false)
  const clusterOk  = corosyncOk && quorumOk
  const aiScore    = dernier_lstm.score || 0
  const aiColor    = aiScore > 0.8 ? C.red : aiScore > 0.5 ? C.orange : C.green
  const aiLabel    = aiScore > 0.8 ? 'CRITICAL' : aiScore > 0.5 ? 'SUSPICIOUS' : 'NORMAL'

  const chartData = history.slice(-50).map((h, i) => {
    if (chartScope === 'cluster') {
      const ns = h.noeuds || []
      return {
        i,
        CPU:    ns.length ? ns.reduce((s, n) => s + (n.cpu || 0), 0) / ns.length : 0,
        Memory: ns.length ? ns.reduce((s, n) => s + (n.ram || 0), 0) / ns.length : 0,
      }
    }
    const match = (h.noeuds || []).find(n => n.nom === chartScope)
    return { i, CPU: match?.cpu ?? 0, Memory: match?.ram ?? 0 }
  })

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>

      {/* ── Bandeau statut global ─────────────────────────────────────────── */}
      <div style={{ background: healthColor + '0d', border: `1px solid ${healthColor}30`, borderRadius: 10, padding: '12px 20px', display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: 12 }}>
          <OnlineDot online={health === 'OPERATIONAL'}/>
          <span style={{ fontWeight: 800, color: healthColor, fontSize: 15 }}>System Status: {health}</span>
          <span style={{ color: C.sub, fontSize: 13 }}>Last check: {new Date().toLocaleTimeString('fr-FR')}</span>
        </div>
        <div style={{ display: 'flex', gap: 16, fontSize: 11, color: C.sub, fontFamily: 'JetBrains Mono, monospace', alignItems: 'center' }}>
          {clusterUptimeH > 0 && <span>↑ {fmtUptime(clusterUptimeH)}</span>}
          <span style={{ padding: '2px 8px', borderRadius: 6, fontSize: 10, fontWeight: 700, background: aiColor + '18', border: `1px solid ${aiColor}40`, color: aiColor }}>
            AI {(aiScore * 100).toFixed(0)}% — {aiLabel}
          </span>
          {!clusterOk && <span style={{ color: C.red, fontWeight: 700 }}>⚠ QUORUM LOST</span>}
          <span style={{ color: C.muted }}>|</span>
          <span>{noeuds.length} Nodes</span>
          <span style={{ color: C.green }}>{vmsRunning.length} VMs up</span>
          {vmsStopped.length > 0 && <span style={{ color: C.muted }}>{vmsStopped.length} stopped</span>}
          {alertes.length > 0 && <span style={{ color: C.orange }}>{alertes.length} alert(s)</span>}
        </div>
      </div>

      {/* ── 5 KPI cards ──────────────────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(5,1fr)', gap: 12 }}>
        {[
          { label: 'Avg CPU Load',      value: avgCPU.toFixed(1) + '%', color: riskColor(avgCPU),   sub: `${noeudsActifs.length}/${noeuds.length} nodes online` },
          { label: 'Avg Memory Usage',  value: avgRAM.toFixed(1) + '%', color: riskColor(avgRAM),   sub: noeudsActifs.length < noeuds.length ? `${noeudsActifs.length} active nodes` : 'across cluster' },
          { label: 'Cluster Uptime',    value: clusterUptimeH > 0 ? fmtUptime(clusterUptimeH) : '—', color: clusterUptimeH > 0 ? C.green : C.muted, sub: clusterUptimeH > 0 ? 'since last reboot' : 'no data yet' },
          { label: 'VMs Running',       value: `${vmsRunning.length}/${vms.length}`, color: vmsRunning.length === vms.length && vms.length > 0 ? C.green : vmsRunning.length === 0 ? C.muted : C.orange, sub: vmsStopped.length > 0 ? `${vmsStopped.length} stopped` : 'all running' },
          { label: 'Open Incidents',    value: incidents.length, color: incidents.length ? C.red : C.green, sub: `${critAlertes.length} critical` },
        ].map(({ label, value, color, sub }) => (
          <Card key={label} glow={color !== C.green && color !== C.muted ? color : undefined} style={{ padding: '16px 18px' }}>
            <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.1em', marginBottom: 8 }}>{label.toUpperCase()}</div>
            <div style={{ fontSize: 28, fontWeight: 800, color, fontFamily: 'JetBrains Mono, monospace', marginBottom: 4 }}>{value}</div>
            <div style={{ fontSize: 12, color: C.sub }}>{sub}</div>
          </Card>
        ))}
      </div>

      {/* ── Alerte Quorum ────────────────────────────────────────────────── */}
      {!clusterOk && (
        <div style={{ background: C.red + '0d', border: `1px solid ${C.red}50`, borderRadius: 10, padding: '12px 20px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <span style={{ fontSize: 18 }}>⚠</span>
          <div>
            <div style={{ fontWeight: 700, color: C.red, fontSize: 13 }}>
              {!quorumOk ? 'CLUSTER QUORUM LOST — VMs may shut down automatically' : 'Corosync degraded — cluster communication impaired'}
            </div>
            <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginTop: 3 }}>
              Run: pvecm status · corosync-cfgtool -s · journalctl -u corosync
            </div>
          </div>
        </div>
      )}

      {/* ── 3 indicateurs compacts ───────────────────────────────────────── */}
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: 12 }}>
        <Card style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: clusterOk ? C.green : C.red, flexShrink: 0 }}/>
          <div>
            <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.08em', marginBottom: 2 }}>COROSYNC / QUORUM</div>
            <div style={{ fontSize: 13, fontWeight: 700, color: clusterOk ? C.green : C.red }}>
              {clusterOk ? 'Healthy' : !quorumOk ? 'QUORUM LOST' : 'Degraded'}
            </div>
            <div style={{ fontSize: 10, color: C.muted }}>{noeudsActifs.length}/{noeuds.length} nodes in quorum</div>
          </div>
        </Card>
        <Card style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: aiColor, flexShrink: 0 }}/>
          <div style={{ flex: 1 }}>
            <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.08em', marginBottom: 2 }}>AI ANOMALY SCORE</div>
            <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
              <div style={{ fontSize: 14, fontWeight: 700, color: aiColor }}>{aiLabel}</div>
              <div style={{ fontSize: 16, fontWeight: 800, color: aiColor, fontFamily: 'JetBrains Mono, monospace' }}>{(aiScore * 100).toFixed(0)}%</div>
            </div>
            <div style={{ height: 2, background: C.border, borderRadius: 1, marginTop: 6 }}>
              <div style={{ height: '100%', width: `${aiScore * 100}%`, background: aiColor, borderRadius: 1, transition: 'width 1s ease' }}/>
            </div>
          </div>
        </Card>
        <Card style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
          <div style={{ width: 8, height: 8, borderRadius: '50%', background: C.blue, flexShrink: 0 }}/>
          <div>
            <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.08em', marginBottom: 2 }}>CLUSTER UPTIME</div>
            <div style={{ fontSize: 14, fontWeight: 800, color: C.text }}>{clusterUptimeH > 0 ? fmtUptime(clusterUptimeH) : '—'}</div>
            <div style={{ fontSize: 10, color: C.muted }}>{noeudsActifs.length > 0 ? 'longest running node' : 'no active nodes'}</div>
          </div>
        </Card>
      </div>

      {/* ── Graphique performance ────────────────────────────────────────── */}
      <Card style={{ padding: '16px 18px' }}>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 14 }}>
          <SectionLabel>Performance — CPU & Memory (last 50 samples)</SectionLabel>
          <div style={{ display: 'flex', gap: 4, background: C.bg, borderRadius: 7, padding: 3 }}>
            {['cluster', ...noeuds.map(n => n.nom)].map(scope => (
              <button key={scope} onClick={() => setChartScope(scope)}
                style={{ padding: '4px 11px', borderRadius: 5, fontSize: 12, fontFamily: 'JetBrains Mono, monospace', fontWeight: 700, border: 'none', cursor: 'pointer', background: chartScope === scope ? C.blue : 'transparent', color: chartScope === scope ? '#fff' : C.sub, transition: 'all 0.15s' }}>
                {scope === 'cluster' ? 'Cluster' : scope}
              </button>
            ))}
          </div>
        </div>
        <ResponsiveContainer width="100%" height={150}>
          <AreaChart data={chartData} margin={{ top: 4, right: 4, bottom: 0, left: -24 }}>
            <defs>
              <linearGradient id="gCPU" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={C.blue} stopOpacity={0.3}/>
                <stop offset="95%" stopColor={C.blue} stopOpacity={0}/>
              </linearGradient>
              <linearGradient id="gMEM" x1="0" y1="0" x2="0" y2="1">
                <stop offset="5%" stopColor={C.purple} stopOpacity={0.25}/>
                <stop offset="95%" stopColor={C.purple} stopOpacity={0}/>
              </linearGradient>
            </defs>
            <CartesianGrid stroke={C.border} strokeDasharray="3 3"/>
            <XAxis dataKey="i" tick={false} axisLine={false}/>
            <YAxis domain={[0, 100]} tick={{ fill: C.muted, fontSize: 10 }} axisLine={false} tickLine={false}/>
            <Tooltip content={<ChartTip/>}/>
            <ReferenceLine y={80} stroke={C.orange} strokeDasharray="3 3" opacity={0.4}/>
            <Area type="monotone" dataKey="CPU"    stroke={C.blue}   fill="url(#gCPU)" strokeWidth={1.5} dot={false} isAnimationActive={false}/>
            <Area type="monotone" dataKey="Memory" stroke={C.purple} fill="url(#gMEM)" strokeWidth={1.5} dot={false} isAnimationActive={false}/>
          </AreaChart>
        </ResponsiveContainer>
      </Card>

      {/* ── Résumé par nœud ──────────────────────────────────────────────── */}
      <div>
        <SectionLabel>Node Health Summary</SectionLabel>
        <div style={{ display: 'grid', gridTemplateColumns: `repeat(${Math.min(noeuds.length, 3)}, 1fr)`, gap: 12 }}>
          {noeuds.map((n, i) => <NodeSummaryCard key={i} n={n}/>)}
        </div>
        <div style={{ fontSize: 12, color: C.muted, marginTop: 8, display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ color: C.borderHi }}>▸</span>
          <span>Detailed per-node metrics available in the</span>
          <span style={{ color: C.blue, fontWeight: 600, fontFamily: 'JetBrains Mono, monospace', fontSize: 11 }}>Infrastructure</span>
          <span>page — IOPS, latency, SMART, ZFS, network.</span>
        </div>
      </div>

      {/* ── VMs ──────────────────────────────────────────────────────────── */}
      <Card style={{ padding: '16px 18px' }}>
        <SectionLabel>Virtual Machines ({vms.length})</SectionLabel>
        {vms.length === 0 ? (
          <div style={{ color: C.muted, fontSize: 12, fontStyle: 'italic' }}>No VMs detected on this cluster.</div>
        ) : (
          <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
            {vms.map((v, i) => {
              const running = v.statut === 'running' || v.statut === 'en cours'
              return (
                <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '9px 14px', borderRadius: 8, background: C.bg, border: `1px solid ${C.border}`, opacity: running ? 1 : 0.65 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                    <OnlineDot online={running}/>
                    <span style={{ fontSize: 14, color: C.text, fontWeight: 700 }}>{v.nom}</span>
                    <span style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>VMID {v.vmid} · {v.noeud}</span>
                  </div>
                  <div style={{ display: 'flex', gap: 14, alignItems: 'center', fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>
                    {running ? (
                      <>
                        <span style={{ color: riskColor(v.cpu_pct || 0) }}>CPU {(v.cpu_pct || 0).toFixed(0)}%</span>
                        <span style={{ color: riskColor(v.ram_pct || 0) }}>RAM {(v.ram_pct || 0).toFixed(0)}%</span>
                        <span style={{ color: C.sub }}>↑ {fmtUptime(v.uptime_h)}</span>
                      </>
                    ) : (
                      <span style={{ color: C.muted }}>STOPPED</span>
                    )}
                  </div>
                </div>
              )
            })}
          </div>
        )}
      </Card>

      {/* ── Active Alerts — visible seulement si incidents ───────────────── */}
      {alertes.length > 0 && (
        <Card style={{ padding: '16px 18px' }} glow={C.red}>
          <SectionLabel color={C.red}>Active Alerts</SectionLabel>
          <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
            {alertes.map((a, i) => (
              <div key={i} style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', background: C.bg, borderRadius: 8, padding: '10px 14px', border: `1px solid ${severityColor(a.niveau)}25` }}>
                <div style={{ display: 'flex', alignItems: 'center', gap: 10 }}>
                  <span style={{ width: 6, height: 6, borderRadius: '50%', background: severityColor(a.niveau), flexShrink: 0 }}/>
                  <span style={{ fontSize: 14, color: C.text }}>{a.message}</span>
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