import { useState, useEffect } from 'react'
import { C } from '../utils/colors'
import { fmtUptime } from '../utils/formatters'
import { Card } from '../components/Card'
import { Chip, Placeholder } from '../components/Common'

function Dot({ on }) {
  return <span style={{ width:7, height:7, borderRadius:'50%', display:'inline-block', background: on ? C.green : C.red, flexShrink:0 }}/>
}
function Bar({ val = 0, color }) {
  return (
    <div style={{ width:'100%', height:5, background:C.bg, borderRadius:3 }}>
      <div style={{ width:`${Math.min(100, Math.max(0, val))}%`, height:'100%', background:color, borderRadius:3, transition:'width 0.6s ease' }}/>
    </div>
  )
}

// ── Tuile horizontale (étiquette, barre optionnelle, valeur) -- même schéma
// visuel que les tuiles CPU/RAM/DISK déjà utilisées dans la liste des
// nœuds/VMs plus bas dans ce fichier. barVal absent = pas de barre (utile
// pour une valeur sans échelle 0-100 naturelle, ex: nombre de VMs).
function MetricTile({ label, value, unit='', color=C.sub, barVal=null, barColor=null, secondary=null }) {
  return (
    <div>
      <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>{label}</div>
      {barVal != null && <Bar val={barVal} color={barColor || color}/>}
      <div style={{ fontSize:12, color, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>
        {value}{unit}
      </div>
      {secondary != null && (
        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', marginTop:2 }}>
          {secondary}
        </div>
      )}
    </div>
  )
}

// ── Badges de rôle, à partir des tags Proxmox natifs (qm set --tags) ──────────
function RoleTags({ tags }) {
  if (!tags) return null
  const list = tags.split(/[,;]\s*/).filter(Boolean)
  if (!list.length) return null
  return (
    <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginTop:6 }}>
      {list.map((t, i) => (
        <span key={i} style={{ fontSize:10, fontWeight:700, color:C.blue, background:C.blue+'18', border:`1px solid ${C.blue}40`, borderRadius:10, padding:'2px 9px', fontFamily:'JetBrains Mono,monospace', textTransform:'uppercase', letterSpacing:'0.03em' }}>
          {t}
        </span>
      ))}
    </div>
  )
}

// ── Badges "détecté en direct" via Prometheus ──────────────────────────────────
function DetectedServices({ services, selected = null, onSelect = null }) {
  if (!services || !services.length) return null
  const clickable = !!onSelect
  return (
    <div style={{ display:'flex', gap:6, flexWrap:'wrap', marginTop:6 }}>
      {services.map((s, i) => {
        const isSel = selected === s
        return (
          <span key={i}
            onClick={clickable ? (e) => { e.stopPropagation(); onSelect(isSel ? null : s) } : undefined}
            style={{
              display:'flex', alignItems:'center', gap:4, fontSize:10, fontWeight:700,
              color: isSel ? '#0a0f14' : '#22c55e',
              background: isSel ? '#22c55e' : '#22c55e18',
              border:'1px solid #22c55e40', borderRadius:10, padding:'2px 9px',
              fontFamily:'JetBrains Mono,monospace',
              cursor: clickable ? 'pointer' : 'default',
              transition:'all 0.15s',
            }}>
            <span style={{ width:5, height:5, borderRadius:'50%', background: isSel ? '#0a0f14' : '#22c55e' }}/>
            {s}
          </span>
        )
      })}
    </div>
  )
}

function InfraMetricRow({ label, value, unit='', warn=75, crit=90, isTemp=false, isBool=null, isOk=null, colorOverride=null }) {
  const num = parseFloat(value) || 0
  let color = C.green
  if (colorOverride)      { color = colorOverride }
  else if (isBool !== null) { color = isBool ? C.green : C.red }
  else if (isOk !== null)   { color = isOk ? C.green : C.red }
  else if (num >= crit) color = C.red
  else if (num >= warn) color = C.yellow
  const showBar = isBool === null && isOk === null && unit === '%'
  return (
    <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'6px 0', borderBottom:`1px solid ${C.border}20` }}>
      <span style={{ fontSize:13, color:C.sub }}>{label}</span>
      <div style={{ display:'flex', alignItems:'center', gap:10 }}>
        {showBar && (
          <div style={{ width:80, height:4, background:C.bg, borderRadius:2 }}>
            <div style={{ width:`${Math.min(num,100)}%`, height:'100%', background:color, borderRadius:2, transition:'width 0.5s' }}/>
          </div>
        )}
        <span style={{ fontSize:13, fontWeight:700, color, fontFamily:'JetBrains Mono,monospace', minWidth:60, textAlign:'right' }}>
          {isBool !== null ? (isBool ? '✓ OK' : '✗ FAIL') :
           isOk  !== null ? (isOk  ? '✓ OK' : '✗ FAIL') :
           `${value}${unit}`}
        </span>
      </div>
    </div>
  )
}

// ── Clés déjà affichées explicitement dans ServiceDetailCard -- tout le
// reste (connections_active, waiting_locks, cache_hit_pct... apportés par
// un enrichisseur dédié côté backend, ex: PostgreSQL) s'affiche
// automatiquement via ExtraServiceMetrics, sans code par service ici.
const CLES_CONNUES_SERVICE = new Set(['ram_mb', 'cpu_pct', 'cpu_pct_vm', 'disk_read_mbps', 'disk_write_mbps', 'num_procs'])

function humaniser(cle) {
  return cle.replace(/_pct$/, '').replace(/_ms$/, '').replace(/_gb$/, '').replace(/_/g, ' ')
    .replace(/\b\w/g, c => c.toUpperCase())
}

// ← Affiche automatiquement toute métrique interne additionnelle apportée
// par un enrichisseur dédié côté backend (ENRICHISSEURS_SERVICE dans
// vm_app_monitor.py -- ex: connexions/locks/cache hit PostgreSQL, espace
// disque Docker). Générique : un futur enrichisseur Redis/MySQL apparaît
// de la même façon sans toucher ce composant.
function ExtraServiceMetrics({ m }) {
  const entries = Object.entries(m || {}).filter(
    ([k, v]) => !CLES_CONNUES_SERVICE.has(k) && v !== null && v !== undefined
  )
  if (!entries.length) return null
  return (
    <>
      {entries.map(([k, v]) => {
        if (k === 'up') {
          return <InfraMetricRow key={k} label="Up" value="" isOk={v === 1 || v === 1.0}/>
        }
        if (k === 'scrape_ok') {
          return <InfraMetricRow key={k} label="Scrape reachable" value="" isOk={v === 1 || v === 1.0}/>
        }
        if (typeof v === 'boolean') {
          return <InfraMetricRow key={k} label={humaniser(k)} value="" isBool={v}/>
        }
        const unit = k.endsWith('_pct') ? '%' : k.endsWith('_ms') ? 'ms' : k.endsWith('_gb') ? ' GB' : ''
        const val  = typeof v === 'number' ? Math.round(v * 100) / 100 : v
        return <InfraMetricRow key={k} label={humaniser(k)} value={val} unit={unit} warn={99999} crit={99999}/>
      })}
    </>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// ← AJOUT : même principe qu'ExtraServiceMetrics ci-dessus, appliqué au
// niveau NŒUD et VM -- avant, seules les métriques explicitement codées en
// dur dans NodeDetail/VmDetail (une <InfraMetricRow> écrite à la main par
// champ) apparaissaient à l'écran. Une métrique réellement collectée par
// metriques_proxmox.py (etat["noeuds"][i]["fd_used_pct"], par exemple --
// confirmé présente dans les données, jamais affichée nulle part sur cette
// page) restait invisible tant que personne n'ajoutait sa ligne à la main.
// Exactement l'écart entre "collecté automatiquement" et "affiché
// automatiquement" -- fermé ici de la même façon que pour les services,
// déjà prouvée fonctionner. Toute métrique future ajoutée à
// metriques_proxmox.py (ex: total_vms_sans_sauvegarde) apparaît
// désormais ici sans toucher ce fichier.
// ══════════════════════════════════════════════════════════════════════════════
const CLES_CONNUES_NOEUD = new Set([
  'node', 'nom', 'timestamp', 'statut',
  'cpu_pct', 'cpu_iowait_pct', 'cpu_steal_pct', 'load_avg_1m', 'cpu_cores', 'cpu_temp_max_c',
  'ram_pct', 'ram_used_gb', 'ram_total_gb',
  'swap_pct', 'swap_used_gb',
  'disk_pct', 'disk_used_gb', 'disk_total_gb', 'disk_read_iops', 'disk_write_iops',
  'disk_read_latency_ms', 'disk_write_latency_ms',
  'zfs_available', 'zfs_arc_hit_rate', 'zfs_arc_size_gb',
  'smart_ok', 'smart_disks_monitored',
  'net_in_mbps', 'net_out_mbps', 'net_errors_in', 'net_errors_out', 'net_drop_in', 'net_drop_out',
  'uptime_h', 'vms_running',
  'corosync_available', 'corosync_ok', 'corosync_quorum_ok', 'corosync_ring_latency_ms',
  'power_watts',
])

function ExtraNodeMetrics({ n }) {
  const entries = Object.entries(n || {}).filter(
    ([k, v]) => !CLES_CONNUES_NOEUD.has(k) && v !== null && v !== undefined && typeof v !== 'object'
  )
  if (!entries.length) return null
  return (
    <>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>OTHER</div>
      {entries.map(([k, v]) => {
        if (typeof v === 'boolean') {
          return <InfraMetricRow key={k} label={humaniser(k)} value="" isBool={v}/>
        }
        const unit = k.endsWith('_pct') ? '%' : k.endsWith('_ms') ? 'ms' : k.endsWith('_gb') ? ' GB'
          : k.endsWith('_mbps') ? ' MB/s' : k.endsWith('_c') ? '°C' : k.endsWith('_watts') ? ' W' : ''
        const val = typeof v === 'number' ? Math.round(v * 100) / 100 : v
        return <InfraMetricRow key={k} label={humaniser(k)} value={val} unit={unit} warn={99999} crit={99999}/>
      })}
    </>
  )
}

const CLES_CONNUES_VM = new Set([
  'vmid', 'nom', 'name', 'node', 'noeud', 'type', 'statut',
  'cpu_pct', 'vcpus',
  'ram_pct', 'ram_used_gb', 'ram_total_gb', 'maxmem_gb',
  'disk_pct', 'disk_used_gb', 'disk_total_gb', 'maxdisk_gb',
  'disk_read_mbps', 'disk_write_mbps',
  'net_in_mbps', 'net_out_mbps',
  'uptime_h', 'tags', 'services_detectes', 'metriques_services',
])

function ExtraVmMetrics({ v }) {
  const entries = Object.entries(v || {}).filter(
    ([k, val]) => !CLES_CONNUES_VM.has(k) && val !== null && val !== undefined && typeof val !== 'object'
  )
  if (!entries.length) return null
  return (
    <>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>OTHER</div>
      {entries.map(([k, val]) => {
        if (typeof val === 'boolean') {
          return <InfraMetricRow key={k} label={humaniser(k)} value="" isBool={val}/>
        }
        const unit = k.endsWith('_pct') ? '%' : k.endsWith('_ms') ? 'ms' : k.endsWith('_gb') ? ' GB'
          : k.endsWith('_mbps') ? ' MB/s' : k.endsWith('_c') ? '°C' : ''
        const v2 = typeof val === 'number' ? Math.round(val * 100) / 100 : val
        return <InfraMetricRow key={k} label={humaniser(k)} value={v2} unit={unit} warn={99999} crit={99999}/>
      })}
    </>
  )
}

// ── Carte séparée : métriques réelles du service sélectionné (process-exporter) ─
function ServiceDetailCard({ service, metriques, onClose }) {
  if (!service || !metriques || !metriques[service]) return null
  const m = metriques[service]
  return (
    <Card style={{ padding:'16px 20px', position:'sticky', top:20, maxHeight:'85vh', overflowY:'auto' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
        <div>
          <div style={{ fontSize:16, fontWeight:800, color:C.text }}>{service}</div>
          <div style={{ fontSize:12, color:C.muted }}>Service — Live metrics</div>
        </div>
        <button onClick={onClose}
          style={{ background:'transparent', border:`1px solid ${C.border}`, color:C.muted, borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:12 }}>
          ✕ Close
        </button>
      </div>
      <InfraMetricRow label="RAM"        value={m.ram_mb ?? 0}          unit=" MB"   warn={99999} crit={99999}/>
      <InfraMetricRow label="CPU"        value={m.cpu_pct ?? 0}         unit="%"     warn={80}    crit={95}/>
      {m.cpu_pct_vm != null && (
        <InfraMetricRow label="CPU (% of VM capacity)" value={m.cpu_pct_vm} unit="%" warn={65} crit={80}/>
      )}
      <InfraMetricRow label="Disk Read"  value={m.disk_read_mbps ?? 0}  unit=" MB/s" warn={99999} crit={99999}/>
      <InfraMetricRow label="Disk Write" value={m.disk_write_mbps ?? 0} unit=" MB/s" warn={99999} crit={99999}/>
      {m.num_procs != null && (
        <InfraMetricRow label="Processes" value={m.num_procs} unit="" warn={99999} crit={99999}/>
      )}
      <ExtraServiceMetrics m={m}/>
    </Card>
  )
}

function HypervisorBadge({ hyperviseur, metriques, isSelected, onSelect }) {
  if (!hyperviseur || hyperviseur.type === 'unknown') return null
  const accent = '#a855f7'
  const cpuColor  = (metriques?.vms_cpu_pct_hote??0)        >= 90 ? C.red : (metriques?.vms_cpu_pct_hote??0)        >= 80 ? C.yellow : C.green
  const ramColor  = (metriques?.vms_ram_allouee_pct_hote??0) >= 85 ? C.red : (metriques?.vms_ram_allouee_pct_hote??0) >= 75 ? C.yellow : C.green
  const diskColor = (metriques?.vms_disk_allouee_pct_hote??0)>= 90 ? C.red : (metriques?.vms_disk_allouee_pct_hote??0)>= 80 ? C.yellow : C.green
  return (
    <div onClick={onSelect}
      style={{ background:isSelected?C.surface:C.card, border:`1px solid ${isSelected?accent+'60':C.border}`, borderRadius:10, padding:'14px 16px', cursor:'pointer', transition:'all 0.15s' }}
      onMouseEnter={e=>e.currentTarget.style.borderColor=accent+'40'}
      onMouseLeave={e=>e.currentTarget.style.borderColor=isSelected?accent+'60':C.border}
    >
      <div style={{ display:'flex', alignItems:'center', gap:7, marginBottom: metriques?.disponible ? 4 : 0 }}>
        <span style={{ width:6, height:6, borderRadius:'50%', background:accent, flexShrink:0 }}/>
        <span style={{ fontSize:12, color:C.muted }}>Running on</span>
        <span style={{ fontSize:13, fontWeight:700, color:C.text }}>{hyperviseur.produit}</span>
      </div>
      {metriques && metriques.disponible && (<>
        <div style={{ fontSize:10, color:C.muted, fontStyle:'italic', marginBottom:8 }}>
          % relative to total PC capacity
        </div>
        <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:'12px 16px' }}>
          <MetricTile
            label="CPU" barVal={metriques.vms_cpu_pct_hote} color={cpuColor}
            value={metriques.vms_cpu_pct_hote} unit="%"
          />
          {metriques.vms_ram_allouee_pct_hote != null && (
            <MetricTile
              label="RAM" barVal={metriques.vms_ram_allouee_pct_hote} color={ramColor}
              value={metriques.vms_ram_allouee_pct_hote} unit="%"
              secondary={`${metriques.vms_ram_allouee_gb} GB allocated`}
            />
          )}
          {metriques.vms_disk_allouee_pct_hote != null && (
            <MetricTile
              label="DISK" barVal={metriques.vms_disk_allouee_pct_hote} color={diskColor}
              value={metriques.vms_disk_allouee_pct_hote} unit="%"
              secondary={`${metriques.vms_disk_allouee_gb} GB allocated`}
            />
          )}
        </div>
        <div style={{ display:'flex', gap:16, marginTop:10, flexWrap:'wrap' }}>
          <span style={{ fontSize:11, color:C.muted }}>{metriques.vm_count} VM{metriques.vm_count>1?'s':''} running</span>
          {metriques.marge_reelle_ram_gb != null && (
            <span style={{ fontSize:11, color: metriques.marge_reelle_ram_gb < 0 ? C.red : C.muted }}>
              Real headroom: {metriques.marge_reelle_ram_gb}GB
            </span>
          )}
          {metriques.vmdk_reel_gb != null && (
            <span style={{ fontSize:11, color:C.muted }}>Real disk usage: {metriques.vmdk_reel_gb}GB</span>
          )}
        </div>
      </>)}
    </div>
  )
}

function HostPcBadge({ hote, isSelected, onSelect }) {
  if (!hote || !hote.disponible) return null
  const accent = '#f59e0b'
  const cpuColor  = (hote.cpu_pct_used??0)  >= 90 ? C.red : (hote.cpu_pct_used??0)  >= 80 ? C.yellow : C.green
  const ramColor  = (hote.ram_pct_used??0)  >= 85 ? C.red : (hote.ram_pct_used??0)  >= 75 ? C.yellow : C.green
  const diskColor = (hote.disk_pct_used??0) >= 90 ? C.red : (hote.disk_pct_used??0) >= 80 ? C.yellow : C.green
  return (
    <div onClick={onSelect}
      style={{ background:isSelected?C.surface:C.card, border:`1px solid ${isSelected?accent+'60':C.border}`, borderRadius:10, padding:'14px 16px', cursor:'pointer', transition:'all 0.15s' }}
      onMouseEnter={e=>e.currentTarget.style.borderColor=accent+'40'}
      onMouseLeave={e=>e.currentTarget.style.borderColor=isSelected?accent+'60':C.border}
    >
      <div style={{ display:'flex', alignItems:'center', gap:7, marginBottom:12 }}>
        <span style={{ width:6, height:6, borderRadius:'50%', background:accent, flexShrink:0 }}/>
        <span style={{ fontSize:13, fontWeight:700, color:C.text }}>Host PC</span>
      </div>
      <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:'12px 16px' }}>
        <MetricTile label="CPU"  barVal={hote.cpu_pct_used}  color={cpuColor}  value={hote.cpu_pct_used}  unit="%"/>
        <MetricTile label="RAM"  barVal={hote.ram_pct_used}  color={ramColor}  value={hote.ram_pct_used}  unit="%" secondary={`${hote.ram_used_gb}/${hote.ram_total_gb} GB`}/>
        <MetricTile label="DISK" barVal={hote.disk_pct_used} color={diskColor} value={hote.disk_pct_used} unit="%" secondary={`${hote.disk_free_gb} GB free`}/>
      </div>
    </div>
  )
}

function NodeDetail({ n, vms = [] }) {
  const vmsOnThisNode = vms.filter(v => (v.noeud || v.node) === (n.nom || n.node))
  const vmsRunningHere = vmsOnThisNode.filter(v => v.statut === 'running').length
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>COMPUTE</div>
      <InfraMetricRow label="CPU Usage"           value={n.cpu_pct??0}              unit="%" warn={65} crit={80}/>
      <InfraMetricRow label="CPU I/O Wait"        value={n.cpu_iowait_pct??0}       unit="%" warn={15} crit={30}/>
      {(n.cpu_steal_pct??0) > 0 && (
        <InfraMetricRow label="CPU Steal"         value={n.cpu_steal_pct??0}        unit="%" warn={10} crit={20}/>
      )}
      <InfraMetricRow label="Load Average 1m"     value={n.load_avg_1m??0}          unit=""  warn={80} crit={95}/>
      <InfraMetricRow label="CPU Cores"           value={n.cpu_cores??'?'}          unit=""  warn={999}/>
      {(n.cpu_temp_max_c??0) > 0 && (
        <InfraMetricRow label="CPU Temperature"   value={n.cpu_temp_max_c??0}       unit="°C" warn={75} crit={85} isTemp/>
      )}

      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>MEMORY</div>
      <InfraMetricRow label="RAM Usage"           value={n.ram_pct??0}              unit="%" warn={75} crit={85}/>
      <InfraMetricRow label="RAM Used / Total"    value={`${n.ram_used_gb??0} / ${n.ram_total_gb??0} GB`} unit="" warn={999}/>
      <InfraMetricRow label="Swap Usage"          value={n.swap_pct??0}             unit="%" warn={50} crit={80}/>
      {(n.swap_used_gb??0) > 0 && (
        <InfraMetricRow label="Swap Used"         value={`${n.swap_used_gb??0} GB`} unit="" warn={999}/>
      )}

      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>STORAGE</div>
      <InfraMetricRow label="Disk Usage"          value={n.disk_pct??0}             unit="%" warn={80} crit={90}/>
      <InfraMetricRow label="Disk Used / Total"   value={`${n.disk_used_gb??0} / ${n.disk_total_gb??0} GB`} unit="" warn={999}/>
      <InfraMetricRow label="Read IOPS"           value={n.disk_read_iops??0}       unit=""  warn={8000} crit={9000}/>
      <InfraMetricRow label="Write IOPS"          value={n.disk_write_iops??0}      unit=""  warn={8000} crit={9000}/>
      <InfraMetricRow label="Read Latency"        value={n.disk_read_latency_ms??0}  unit="ms" warn={10} crit={50}/>
      <InfraMetricRow label="Write Latency"       value={n.disk_write_latency_ms??0} unit="ms" warn={10} crit={50}/>
      {n.zfs_available && (<>
        <InfraMetricRow label="ZFS ARC Hit Rate"  value={n.zfs_arc_hit_rate??0}     unit="%" warn={70} crit={50}/>
        <InfraMetricRow label="ZFS ARC Size"      value={`${n.zfs_arc_size_gb??0} GB`} unit="" warn={999}/>
      </>)}
      {n.smart_disks_monitored > 0 && (
        <InfraMetricRow label="Disk SMART Health" value="" isBool={n.smart_ok??true}/>
      )}

      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>NETWORK</div>
      <InfraMetricRow label="Net In"              value={n.net_in_mbps??0}          unit=" MB/s" warn={800} crit={950}/>
      <InfraMetricRow label="Net Out"             value={n.net_out_mbps??0}         unit=" MB/s" warn={800} crit={950}/>
      <InfraMetricRow label="Net Errors/s"        value={(n.net_errors_in??0)+(n.net_errors_out??0)} unit="" warn={1} crit={10}/>
      <InfraMetricRow label="Packet Drops/s"      value={(n.net_drop_in??0)+(n.net_drop_out??0)}     unit="" warn={1} crit={10}/>

      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>CLUSTER</div>
      <InfraMetricRow label="Uptime"              value={fmtUptime(n.uptime_h)} unit="" warn={999}/>
      <InfraMetricRow label="VMs Running"         value={`${vmsRunningHere}/${vmsOnThisNode.length}`} unit="" warn={999}/>
      {n.corosync_available && (<>
        <InfraMetricRow label="Corosync"          value="" isBool={n.corosync_ok??true}/>
        <InfraMetricRow label="Quorum"            value="" isBool={n.corosync_quorum_ok??true}/>
        <InfraMetricRow label="Ring Latency"      value={n.corosync_ring_latency_ms??0} unit="ms" warn={5} crit={10}/>
      </>)}
      {(n.power_watts??0) > 0 && (
        <InfraMetricRow label="Power"             value={n.power_watts??0}          unit="W" warn={999}/>
      )}

      {/* ← AJOUT : toute métrique collectée par metriques_proxmox.py et
          non explicitement listée ci-dessus s'affiche automatiquement ici
          -- ex: fd_used_pct, procs_blocked, swap_in_rate/out_rate, les
          détails SMART fins, les compteurs corosync détaillés... Rien à
          ajouter à la main ici la prochaine fois qu'une métrique est
          ajoutée côté collecte. */}
      <ExtraNodeMetrics n={n}/>
    </div>
  )
}

function VmDetail({ v, selectedService, onSelectService }) {
  const hasBadges = v.tags || (v.services_detectes && v.services_detectes.length > 0)
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
      {hasBadges && (
        <div style={{ padding:'2px 0 10px 0' }}>
          <RoleTags tags={v.tags}/>
          <DetectedServices services={v.services_detectes} selected={selectedService} onSelect={onSelectService}/>
        </div>
      )}
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>COMPUTE</div>
      <InfraMetricRow label="CPU Usage"      value={v.cpu_pct??0}      unit="%" warn={65} crit={80}/>
      <InfraMetricRow label="vCPUs"          value={v.vcpus??'?'}      unit=""  warn={999}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>MEMORY</div>
      <InfraMetricRow label="RAM Usage"      value={v.ram_pct??0}      unit="%" warn={75} crit={85}/>
      <InfraMetricRow label="RAM Allocated"  value={`${v.ram_used_gb??0} / ${v.ram_total_gb??0} GB`} unit="" warn={999}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>STORAGE</div>
      <InfraMetricRow label="Disk Usage"     value={v.disk_pct??0}     unit="%" warn={80} crit={90}/>
      <InfraMetricRow label="Disk Allocated" value={`${v.disk_used_gb??0} / ${v.disk_total_gb??0} GB`} unit="" warn={999}/>
      <InfraMetricRow label="Read Throughput"  value={v.disk_read_mbps??0}  unit=" MB/s" warn={50} crit={100}/>
      <InfraMetricRow label="Write Throughput" value={v.disk_write_mbps??0} unit=" MB/s" warn={50} crit={100}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>NETWORK</div>
      <InfraMetricRow label="Net In"         value={v.net_in_mbps??0}  unit=" MB/s" warn={800} crit={950}/>
      <InfraMetricRow label="Net Out"        value={v.net_out_mbps??0} unit=" MB/s" warn={800} crit={950}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>INFO</div>
      <InfraMetricRow label="Uptime"         value={fmtUptime(v.uptime_h)} unit="" warn={999}/>
      <InfraMetricRow label="Hosted on"      value={v.noeud??v.node??'?'} unit="" warn={999}/>

      {/* ← AJOUT : même principe que NodeDetail ci-dessus. */}
      <ExtraVmMetrics v={v}/>
    </div>
  )
}

function HostPcDetail({ hote }) {
  if (!hote || !hote.disponible) {
    return <div style={{ fontSize:13, color:C.muted, padding:'12px 0' }}>Host PC metrics not available this cycle.</div>
  }
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>COMPUTE</div>
      <InfraMetricRow label="CPU Usage"      value={hote.cpu_pct_used??0}   unit="%" warn={80} crit={90}/>
      <InfraMetricRow label="CPU Cores"      value={hote.cpu_cores??'?'}    unit=""  warn={999}/>
      {hote.cpu_cores_free != null && (
        <InfraMetricRow label="CPU Cores Free" value={hote.cpu_cores_free} unit="" warn={999}/>
      )}

      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>MEMORY</div>
      <InfraMetricRow label="RAM Usage"        value={hote.ram_pct_used??0} unit="%" warn={75} crit={85}/>
      <InfraMetricRow label="RAM Used / Total" value={`${hote.ram_used_gb??0} / ${hote.ram_total_gb??0} GB`} unit="" warn={999}/>
      <InfraMetricRow label="RAM Available"    value={hote.ram_available_gb??0} unit=" GB" warn={99999} crit={99999}/>

      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>STORAGE</div>
      <InfraMetricRow label="Disk Usage"        value={hote.disk_pct_used??0} unit="%" warn={80} crit={90}/>
      <InfraMetricRow label="Disk Used / Total" value={`${hote.disk_used_gb??0} / ${hote.disk_total_gb??0} GB`} unit="" warn={999}/>
      <InfraMetricRow label="Disk Free"         value={hote.disk_free_gb??0} unit=" GB" warn={99999} crit={99999}/>
    </div>
  )
}

function HypervisorDetail({ hyperviseur, metriques }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
      {hyperviseur.description && (
        <div style={{ fontSize:13, color:C.sub, padding:'2px 0 10px 0' }}>{hyperviseur.description}</div>
      )}
      {!metriques || !metriques.disponible ? (
        <div style={{ fontSize:13, color:C.muted, padding:'12px 0' }}>
          Metrics not yet available for this hypervisor type.
        </div>
      ) : (<>
        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>VIRTUAL MACHINES</div>
        <InfraMetricRow label="VMs Running" value={metriques.vm_count??0} unit="" warn={999}/>

        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>COMPUTE</div>
        <InfraMetricRow label="CPU (% of PC capacity)" value={metriques.vms_cpu_pct_hote??0} unit="%" warn={80} crit={90}/>
        <InfraMetricRow label="CPU (raw, summed per-core)" value={metriques.vms_cpu_pct??0} unit="%" warn={99999} crit={99999}/>
        <InfraMetricRow label="Hypervisor Software Overhead" value={metriques.overhead_ram_mb??0} unit=" MB" warn={99999} crit={99999}/>

        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>MEMORY</div>
        {metriques.vms_ram_allouee_pct_hote != null && (
          <InfraMetricRow label="RAM Allocated (% of PC)" value={metriques.vms_ram_allouee_pct_hote} unit="%" warn={75} crit={85}/>
        )}
        <InfraMetricRow label="RAM Allocated" value={metriques.vms_ram_allouee_gb??0} unit=" GB" warn={99999} crit={99999}/>
        {metriques.marge_reelle_ram_gb != null && (
          <InfraMetricRow
            label="Real Headroom" value={metriques.marge_reelle_ram_gb} unit="GB"
            colorOverride={metriques.marge_reelle_ram_gb < 0 ? C.red : C.green}
          />
        )}

        <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>STORAGE</div>
        {metriques.vms_disk_allouee_pct_hote != null && (
          <InfraMetricRow label="Disk Allocated (% of PC)" value={metriques.vms_disk_allouee_pct_hote} unit="%" warn={80} crit={90}/>
        )}
        <InfraMetricRow label="Disk Allocated" value={metriques.vms_disk_allouee_gb??0} unit=" GB" warn={99999} crit={99999}/>
        {metriques.vmdk_reel_gb != null && (
          <InfraMetricRow label="Real Disk Usage (.vmdk files)" value={metriques.vmdk_reel_gb} unit=" GB" warn={99999} crit={99999}/>
        )}
      </>)}
    </div>
  )
}

export function PageInfrastructure({ cluster }) {
  const [sel, setSel] = useState(null)
  const [selectedService, setSelectedService] = useState(null)

  useEffect(() => { setSelectedService(null) }, [sel])

  if (!cluster) return <Placeholder icon="◉" text="Connecting to cluster..."/>

  const noeuds = cluster.noeuds || []
  const vms    = cluster.vms    || []

  const selectedNode = sel?.startsWith('n:') ? noeuds.find(n=>(n.nom||n.node)===sel.slice(2)) : null
  const selectedVm   = sel?.startsWith('v:') ? vms.find(v=>v.vmid===sel.slice(2))             : null
  const selectedHote = sel === 'hote'
  const selectedHyp  = sel === 'hyp'

  const showServicePanel = !!selectedVm && !!selectedService
    && selectedVm.metriques_services && selectedVm.metriques_services[selectedService]

  const gridCols = showServicePanel ? '1fr 1.2fr 1fr' : (sel ? '1fr 1.4fr' : '1fr')

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div>
          <h2 style={{ fontSize:22, fontWeight:800, marginBottom:6, letterSpacing:'-0.02em',
                         background:'linear-gradient(180deg, #ffffff 0%, #b9c9dd 130%)',
                         WebkitBackgroundClip:'text', WebkitTextFillColor:'transparent' }}>Infrastructure</h2>
          <div style={{ fontSize:13, color:C.sub }}>{noeuds.length} hypervisor{noeuds.length>1?'s':''} · {vms.length} VM{vms.length>1?'s':''} · Click any resource to see all metrics</div>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          <Chip label={`${noeuds.length} NODES`} color={C.blue}/>
          <Chip label={`${vms.filter(v=>v.statut==='running').length} VMs RUNNING`} color={C.green}/>
        </div>
      </div>


      <div style={{ display:'grid', gridTemplateColumns: gridCols, gap:16 }}>
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>

          <HostPcBadge
            hote={cluster.hote_physique}
            isSelected={selectedHote}
            onSelect={()=>setSel(selectedHote?null:'hote')}
          />
          <HypervisorBadge
            hyperviseur={cluster.hyperviseur}
            metriques={cluster.hyperviseur_metriques}
            isSelected={selectedHyp}
            onSelect={()=>setSel(selectedHyp?null:'hyp')}
          />

          <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em', fontWeight:700, padding:'4px 0' }}>HYPERVISORS</div>
          {noeuds.map((n, i) => {
            const nom     = n.nom || n.node || `node-${i}`
            const isOnline= n.statut === 'online' || n.statut === 'Online'
            const isSelN  = sel === `n:${nom}`
            const cpuColor= (n.cpu_pct??0)>=80?C.red:(n.cpu_pct??0)>=65?C.yellow:C.green
            const ramColor= (n.ram_pct??0)>=85?C.red:(n.ram_pct??0)>=75?C.yellow:C.green
            const tempOk  = (n.cpu_temp_max_c??0)===0 || (n.cpu_temp_max_c??0)<75
            return (
              <div key={i} onClick={()=>setSel(isSelN?null:`n:${nom}`)}
                style={{ background:isSelN?C.surface:C.card, border:`1px solid ${isSelN?C.blue+'60':C.border}`, borderRadius:10, padding:'14px 16px', cursor:'pointer', transition:'all 0.15s' }}
                onMouseEnter={e=>e.currentTarget.style.borderColor=C.blue+'40'}
                onMouseLeave={e=>e.currentTarget.style.borderColor=isSelN?C.blue+'60':C.border}
              >
                <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
                  <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
                    <div style={{ fontSize:15, fontWeight:700, color:C.text }}>{nom}</div>
                    <div style={{ fontSize:12, color:C.muted, fontFamily:'JetBrains Mono,monospace', display:'flex', alignItems:'center', gap:6 }}>
                      <span>Proxmox Hypervisor</span>
                      {isOnline ? (<>
                        <span style={{ color:C.border }}>·</span>
                        <span>{n.cpu_cores || '?'} cores</span>
                        <span style={{ color:C.border }}>·</span>
                        <span>{n.ram_total_gb || '?'} GB RAM</span>
                        {(n.uptime_h??0) > 0 && (<><span style={{ color:C.border }}>·</span><span>up {fmtUptime(n.uptime_h)}</span></>)}
                      </>) : (<><span style={{ color:C.border }}>·</span><span style={{ color:C.red }}>Unreachable</span></>)}
                    </div>
                  </div>
                  <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                    {!tempOk && <span style={{ fontSize:12, color:C.orange }}>🌡 {n.cpu_temp_max_c}°C</span>}
                    {!(n.smart_ok??true) && <span style={{ fontSize:12, color:C.red }}>⚠ DISK</span>}
                    {!(n.corosync_ok??true) && <span style={{ fontSize:12, color:C.red }}>⚠ QUORUM</span>}
                    <Dot on={isOnline}/>
                    <span style={{ fontSize:12, color:isOnline?C.green:C.red, fontWeight:700 }}>{isOnline?'Online':'Offline'}</span>
                  </div>
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8 }}>
                  <div>
                    <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>CPU</div>
                    <Bar val={n.cpu_pct??0} color={cpuColor}/>
                    <div style={{ fontSize:12, color:cpuColor, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>{n.cpu_pct??0}%</div>
                  </div>
                  <div>
                    <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>RAM</div>
                    <Bar val={n.ram_pct??0} color={ramColor}/>
                    <div style={{ fontSize:12, color:ramColor, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>{n.ram_used_gb??0}/{n.ram_total_gb??0} GB</div>
                  </div>
                  <div>
                    <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>DISK</div>
                    <Bar val={n.disk_pct??0} color={(n.disk_pct??0)>=90?C.red:(n.disk_pct??0)>=80?C.yellow:C.green}/>
                    <div style={{ fontSize:12, color:C.sub, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>{n.disk_pct??0}%</div>
                  </div>
                </div>
                <div style={{ display:'flex', gap:12, marginTop:10, flexWrap:'wrap' }}>
                  {(n.swap_pct??0)>0 && <span style={{ fontSize:11, color:(n.swap_pct??0)>50?C.orange:C.muted }}>Swap {n.swap_pct??0}%</span>}
                  {(n.cpu_iowait_pct??0)>0 && <span style={{ fontSize:11, color:(n.cpu_iowait_pct??0)>15?C.orange:C.muted }}>I/O wait {n.cpu_iowait_pct??0}%</span>}
                  {(n.cpu_steal_pct??0)>0 && <span style={{ fontSize:11, color:(n.cpu_steal_pct??0)>10?C.orange:C.muted }}>Steal {n.cpu_steal_pct??0}%</span>}
                  {(n.net_in_mbps??0)>0 && <span style={{ fontSize:11, color:C.muted }}>↓{n.net_in_mbps??0} ↑{n.net_out_mbps??0} MB/s</span>}
                  {(n.zfs_arc_hit_rate??0)>0 && <span style={{ fontSize:11, color:(n.zfs_arc_hit_rate??0)<70?C.orange:C.muted }}>ZFS {n.zfs_arc_hit_rate??0}%</span>}
                  {(n.uptime_h??0)>0 && <span style={{ fontSize:11, color:C.muted }}>Up {fmtUptime(n.uptime_h)}</span>}
                </div>
              </div>
            )
          })}

          {vms.length > 0 && (<>
            <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.1em', fontWeight:700, padding:'4px 0', marginTop:4 }}>VIRTUAL MACHINES</div>
            {vms.map((v, i) => {
              const isRunning = v.statut === 'running'
              const isSelV    = sel === `v:${v.vmid}`
              const cpuColor  = (v.cpu_pct??0)>=80?C.red:(v.cpu_pct??0)>=65?C.yellow:C.green
              const ramColor  = (v.ram_pct??0)>=85?C.red:(v.ram_pct??0)>=75?C.yellow:C.green
              return (
                <div key={i} onClick={()=>setSel(isSelV?null:`v:${v.vmid}`)}
                  style={{ background:isSelV?C.surface:C.card, border:`1px solid ${isSelV?C.cyan+'60':C.border}`, borderRadius:10, padding:'14px 16px', cursor:'pointer', transition:'all 0.15s' }}
                  onMouseEnter={e=>e.currentTarget.style.borderColor=C.cyan+'40'}
                  onMouseLeave={e=>e.currentTarget.style.borderColor=isSelV?C.cyan+'60':C.border}
                >
                  <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
                    <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
                      <div style={{ fontSize:15, fontWeight:700, color:C.text }}>{v.nom||v.name||`VM-${v.vmid}`}</div>
                      <div style={{ fontSize:12, color:C.muted, fontFamily:'JetBrains Mono,monospace', display:'flex', alignItems:'center', gap:6 }}>
                        <span>VMID {v.vmid}</span>
                        <span style={{ color:C.border }}>·</span>
                        <span>{v.vcpus??'?'} vCPU</span>
                        <span style={{ color:C.border }}>·</span>
                        <span>{v.maxmem_gb??v.ram_total_gb??'?'} GB RAM</span>
                        <span style={{ color:C.border }}>·</span>
                        <span>{v.noeud||v.node||'?'}</span>
                        {isRunning && (v.uptime_h??0) > 0 && (<><span style={{ color:C.border }}>·</span><span>up {fmtUptime(v.uptime_h)}</span></>)}
                      </div>
                      <RoleTags tags={v.tags}/>
                      <DetectedServices services={v.services_detectes}/>
                    </div>
                    <div style={{ display:'flex', alignItems:'center', gap:8 }}>
                      <Dot on={isRunning}/>
                      <span style={{ fontSize:12, color:isRunning?C.green:C.muted, fontWeight:700 }}>{isRunning?'Running':'Stopped'}</span>
                    </div>
                  </div>
                  <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8 }}>
                    <div>
                      <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>CPU</div>
                      <Bar val={v.cpu_pct??0} color={cpuColor}/>
                      <div style={{ fontSize:12, color:cpuColor, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>{v.cpu_pct??0}%</div>
                    </div>
                    <div>
                      <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>RAM</div>
                      <Bar val={v.ram_pct??0} color={ramColor}/>
                      <div style={{ fontSize:12, color:ramColor, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>{v.ram_used_gb??0}/{v.maxmem_gb??v.ram_total_gb??'?'} GB</div>
                    </div>
                    <div>
                      <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>DISK</div>
                      <Bar val={v.disk_pct??0} color={(v.disk_pct??0)>=90?C.red:C.green}/>
                      <div style={{ fontSize:12, color:C.sub, fontFamily:'JetBrains Mono,monospace', marginTop:3 }}>{v.disk_pct??0}%</div>
                    </div>
                  </div>
                </div>
              )
            })}
          </>)}
        </div>

        {sel && (selectedNode || selectedVm || selectedHote || selectedHyp) && (
          <Card style={{ padding:'16px 20px', position:'sticky', top:20, maxHeight:'85vh', overflowY:'auto' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
              <div>
                <div style={{ fontSize:16, fontWeight:800, color:C.text }}>
                  {selectedNode ? (selectedNode.nom||selectedNode.node) :
                   selectedVm   ? (selectedVm.nom||selectedVm.name||`VM-${selectedVm.vmid}`) :
                   selectedHote ? 'Host PC' :
                   cluster.hyperviseur?.produit}
                </div>
                <div style={{ fontSize:12, color:C.muted }}>
                  {selectedNode ? 'Proxmox Hypervisor — All metrics' :
                   selectedVm   ? `VM ${selectedVm.vmid} — All metrics` :
                   selectedHote ? 'Physical host — All metrics' :
                   'Virtualization layer — All metrics'}
                </div>
              </div>
              <button onClick={()=>setSel(null)}
                style={{ background:'transparent', border:`1px solid ${C.border}`, color:C.muted, borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:12 }}>
                ✕ Close
              </button>
            </div>
            {selectedNode && <NodeDetail n={selectedNode} vms={vms}/>}
            {selectedVm   && (
              <VmDetail
                key={selectedVm.vmid}
                v={selectedVm}
                selectedService={selectedService}
                onSelectService={setSelectedService}
              />
            )}
            {selectedHote && <HostPcDetail hote={cluster.hote_physique}/>}
            {selectedHyp  && <HypervisorDetail hyperviseur={cluster.hyperviseur} metriques={cluster.hyperviseur_metriques}/>}
          </Card>
        )}

        {showServicePanel && (
          <ServiceDetailCard
            service={selectedService}
            metriques={selectedVm.metriques_services}
            onClose={()=>setSelectedService(null)}
          />
        )}
      </div>
    </div>
  )
}