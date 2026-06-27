import { C } from '../utils/colors'
import { fmtUptime } from '../utils/formatters'
import { Card } from './Card'
import { OnlineDot } from './OnlineDot'
import { StatusBadge } from './StatusBadge'
import { MiniGroupLabel } from './Common'
import { MetricLine } from './MetricLine'

export function NodeFullCard({ n }) {
  const ramFree  = (n.ram_total_gb||0) - (n.ram_used_gb||0)
  const diskFree = (n.disk_total_gb||0) * (1 - (n.disk_pct||0)/100)
  // Disponibilite reelle des metriques avancees : flags explicites poses
  // par le backend (agent/surveillance.py) uniquement quand Prometheus a
  // reellement repondu pour ce groupe — pas une simple valeur a zero, qui
  // peut etre une mesure reelle et valide (ex: 0 Mbps de trafic).
  const hasNet   = !!n.net_available
  const hasIO    = !!n.io_available
  const hasZfs   = !!n.zfs_available
  const hasTemp  = !!n.hw_available && (n.cpu_temp_max_c||0) > 0
  const hasSmart = !!n.smart_available
  const netErr   = (n.net_errors_in||0) + (n.net_errors_out||0)
  const netDrop  = (n.net_drop_in||0) + (n.net_drop_out||0)
  const overall  = n.statut === 'online'
    ? (n.cpu_pct>80||n.ram_pct>85||n.disk_pct>90||(n.swap_pct||0)>80||(n.cpu_temp_max_c||0)>85||!n.smart_ok ? 'CRITICAL'
       : n.cpu_pct>65||n.ram_pct>75||n.disk_pct>80||(n.swap_pct||0)>50||(n.cpu_temp_max_c||0)>75 ? 'WARNING' : 'OK')
    : 'OFFLINE'
  const overallColor = { OK:C.green, WARNING:C.orange, CRITICAL:C.red, OFFLINE:C.red }[overall]

  return (
    <Card glow={overallColor} style={{ padding:0, overflow:'hidden' }}>
      {/* En-tete du noeud */}
      <div style={{ padding:'14px 18px', borderBottom:`1px solid ${C.border}`, display:'flex', justifyContent:'space-between', alignItems:'center', background:overallColor+'08' }}>
        <div style={{ display:'flex', alignItems:'center', gap:10 }}>
          <OnlineDot online={n.statut==='online'}/>
          <div>
            <div style={{ fontSize:14, fontWeight:700, color:C.text }}>{n.nom}</div>
            <div style={{ fontSize:10, color:C.muted, fontFamily:'JetBrains Mono, monospace' }}>Proxmox Hypervisor · Uptime {fmtUptime(n.uptime_h)} · {n.cpu_cores||'?'} cores · {n.ram_total_gb||0}GB RAM</div>
          </div>
        </div>
        <StatusBadge label={overall} color={overallColor}/>
      </div>

      {n.statut !== 'online' && (
        <div style={{ padding:'10px 18px', background:C.red+'0d', borderBottom:`1px solid ${C.border}`, fontSize:11, color:C.red, display:'flex', alignItems:'center', gap:8 }}>
          <span>⚠</span>
          <span>Node unreachable or not responding — values below are stale/unavailable, not real measurements. Check network connectivity and Proxmox API timeouts for this node.</span>
        </div>
      )}

      <div style={{ padding:'14px 18px' }}>
        {/* COMPUTE */}
        <MiniGroupLabel>Compute</MiniGroupLabel>
        <MetricLine label="CPU"  display={(n.cpu_pct||0).toFixed(1)} unit="%" value={n.cpu_pct||0} warn={65} crit={80}/>
        <MetricLine label="RAM"  display={`${(n.ram_pct||0).toFixed(1)}% (${(n.ram_used_gb||0).toFixed(1)}/${(n.ram_total_gb||0).toFixed(1)}GB · ${ramFree.toFixed(1)}GB free)`} value={n.ram_pct||0} warn={75} crit={85}/>
        <MetricLine label="Swap" display={`${(n.swap_pct||0).toFixed(1)}%`} value={n.swap_pct||0} warn={50} crit={80}/>
        {n.load_avg_1m != null && n.load_avg_1m > 0 && (
          <MetricLine label="Load avg (1m)" display={n.load_avg_1m.toFixed(2)} value={n.load_avg_1m} max={n.cpu_cores||8} warn={(n.cpu_cores||8)*0.7} crit={n.cpu_cores||8} showBar={false}/>
        )}
        {n.fd_used_pct != null && n.fd_used_pct > 0 && (
          <MetricLine label="File descriptors" display={n.fd_used_pct.toFixed(1)} unit="%" value={n.fd_used_pct} warn={70} crit={90} showBar={false}/>
        )}

        {/* STORAGE */}
        <MiniGroupLabel>Storage</MiniGroupLabel>
        <MetricLine label="Disk usage" display={`${(n.disk_pct||0).toFixed(1)}% (${(n.disk_used_gb||0).toFixed(0)}/${(n.disk_total_gb||0).toFixed(0)}GB · ${diskFree.toFixed(0)}GB free)`} value={n.disk_pct||0} warn={80} crit={90}/>
        {hasIO && (
          <>
            <MetricLine label="CPU I/O wait" display={(n.cpu_iowait_pct||0).toFixed(1)} unit="%" value={n.cpu_iowait_pct||0} warn={15} crit={30}/>
            <MetricLine label="Read / Write IOPS" display={`${(n.disk_read_iops||0).toFixed(0)} / ${(n.disk_write_iops||0).toFixed(0)}`} value={0} showBar={false}/>
            <MetricLine label="Read latency" display={(n.disk_read_latency_ms||0).toFixed(1)} unit="ms" value={n.disk_read_latency_ms||0} warn={10} crit={50} showBar={false}/>
            <MetricLine label="Write latency" display={(n.disk_write_latency_ms||0).toFixed(1)} unit="ms" value={n.disk_write_latency_ms||0} warn={10} crit={50} showBar={false}/>
          </>
        )}
        {hasZfs && (n.zfs_arc_size_gb||0) > 0 && (
          <MetricLine label="ZFS ARC hit rate" display={`${(n.zfs_arc_hit_rate||0).toFixed(1)}% (${(n.zfs_arc_size_gb||0).toFixed(1)}GB cache)`} value={n.zfs_arc_hit_rate||0} warn={70} crit={40} inverse showBar={false}/>
        )}
        {hasZfs && (n.zfs_arc_size_gb||0) === 0 && (
          <div style={{ fontSize:10, color:C.muted, fontStyle:'italic' }}>ZFS module loaded but no active pool/cache detected</div>
        )}
        {!hasIO && !hasZfs && (
          <div style={{ fontSize:10, color:C.muted, fontStyle:'italic' }}>I/O metrics unavailable — install node_exporter</div>
        )}

        {/* NETWORK */}
        <MiniGroupLabel>Network</MiniGroupLabel>
        {hasNet ? (
          <>
            <MetricLine label="Throughput ↓ In / ↑ Out" display={`${(n.net_in_mbps||0).toFixed(2)} / ${(n.net_out_mbps||0).toFixed(2)} MB/s`} value={0} showBar={false}/>
            <MetricLine label="Errors" display={netErr.toFixed(1)} unit="/s" value={netErr} warn={1} crit={10} showBar={false}/>
            <MetricLine label="Packet drops" display={netDrop.toFixed(1)} unit="/s" value={netDrop} warn={1} crit={10} showBar={false}/>
          </>
        ) : (
          <div style={{ fontSize:10, color:C.muted, fontStyle:'italic' }}>Network metrics unavailable</div>
        )}

        {/* HARDWARE HEALTH */}
        <MiniGroupLabel>Hardware Health</MiniGroupLabel>
        {hasTemp && (
          <MetricLine label="CPU temperature" display={(n.cpu_temp_max_c||0).toFixed(0)} unit="°C" value={n.cpu_temp_max_c||0} warn={75} crit={85} showBar={false}/>
        )}
        {hasSmart && (
          <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:11, marginBottom:9 }}>
            <span style={{ color:C.sub }}>SMART ({n.smart_disks_monitored} disk{n.smart_disks_monitored>1?'s':''})</span>
            <StatusBadge label={n.smart_ok ? 'HEALTHY' : 'DEGRADED'} color={n.smart_ok ? C.green : C.red}/>
          </div>
        )}
        {!n.smart_ok && (
          <div style={{ fontSize:10, color:C.red, marginTop:-4, marginBottom:8 }}>
            Reallocated: {n.smart_reallocated_sectors||0} · Uncorrectable: {n.smart_uncorrectable||0}
          </div>
        )}
        {!hasTemp && !hasSmart && (
          <div style={{ fontSize:10, color:C.muted, fontStyle:'italic' }}>Hardware sensors unavailable</div>
        )}
      </div>
    </Card>
  )
}