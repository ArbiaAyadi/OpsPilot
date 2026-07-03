import { useState } from 'react'
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

/* ← SEUL CHANGEMENT : fontSize:12 → fontSize:13 dans label et value */
function InfraMetricRow({ label, value, unit='', warn=75, crit=90, isTemp=false, isBool=null, isOk=null }) {
  const num = parseFloat(value) || 0
  let color = C.green
  if (isBool !== null) { color = isBool ? C.green : C.red }
  else if (isOk !== null) { color = isOk ? C.green : C.red }
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

function NodeDetail({ n, vms = [] }) {
  const vmsOnThisNode = vms.filter(v => (v.noeud || v.node) === (n.nom || n.node))
  const vmsRunningHere = vmsOnThisNode.filter(v => v.statut === 'running').length
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>COMPUTE</div>
      <InfraMetricRow label="CPU Usage"           value={n.cpu_pct??0}              unit="%" warn={65} crit={80}/>
      <InfraMetricRow label="CPU I/O Wait"        value={n.cpu_iowait_pct??0}       unit="%" warn={15} crit={30}/>
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
    </div>
  )
}

function VmDetail({ v }) {
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:0 }}>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>COMPUTE</div>
      <InfraMetricRow label="CPU Usage"      value={v.cpu_pct??0}      unit="%" warn={65} crit={80}/>
      <InfraMetricRow label="vCPUs"          value={v.vcpus??'?'}      unit=""  warn={999}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>MEMORY</div>
      <InfraMetricRow label="RAM Usage"      value={v.ram_pct??0}      unit="%" warn={75} crit={85}/>
      <InfraMetricRow label="RAM Allocated"  value={`${v.ram_used_gb??0} / ${v.ram_total_gb??0} GB`} unit="" warn={999}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>STORAGE</div>
      <InfraMetricRow label="Disk Usage"     value={v.disk_pct??0}     unit="%" warn={80} crit={90}/>
      <InfraMetricRow label="Disk Allocated" value={`${v.disk_used_gb??0} / ${v.disk_total_gb??0} GB`} unit="" warn={999}/>
      <InfraMetricRow label="Read IOPS"      value={v.disk_read_iops??0}  unit="" warn={8000} crit={9000}/>
      <InfraMetricRow label="Write IOPS"     value={v.disk_write_iops??0} unit="" warn={8000} crit={9000}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>NETWORK</div>
      <InfraMetricRow label="Net In"         value={v.net_in_mbps??0}  unit=" MB/s" warn={800} crit={950}/>
      <InfraMetricRow label="Net Out"        value={v.net_out_mbps??0} unit=" MB/s" warn={800} crit={950}/>
      <div style={{ fontSize:11, color:C.muted, fontFamily:'JetBrains Mono,monospace', letterSpacing:'0.08em', padding:'10px 0 6px 0' }}>INFO</div>
      <InfraMetricRow label="Uptime"         value={fmtUptime(v.uptime_h)} unit="" warn={999}/>
      <InfraMetricRow label="Hosted on"      value={v.noeud??v.node??'?'} unit="" warn={999}/>
    </div>
  )
}

export function PageInfrastructure({ cluster }) {
  const [sel, setSel] = useState(null)
  if (!cluster) return <Placeholder icon="◉" text="Connecting to cluster..."/>

  const noeuds = cluster.noeuds || []
  const vms    = cluster.vms    || []

  const selectedNode = sel?.startsWith('n:') ? noeuds.find(n=>(n.nom||n.node)===sel.slice(2)) : null
  const selectedVm   = sel?.startsWith('v:') ? vms.find(v=>v.vmid===sel.slice(2))             : null

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div>
          {/* ← SEUL CHANGEMENT : 18→22, 700→800 */}
          <h2 style={{ fontSize:22, fontWeight:800, color:C.text, marginBottom:6 }}>Infrastructure</h2>
          {/* ← SEUL CHANGEMENT : 12→13 */}
          <div style={{ fontSize:13, color:C.sub }}>{noeuds.length} hypervisor{noeuds.length>1?'s':''} · {vms.length} VM{vms.length>1?'s':''} · Click any resource to see all metrics</div>
        </div>
        <div style={{ display:'flex', gap:8 }}>
          <Chip label={`${noeuds.length} NODES`} color={C.blue}/>
          <Chip label={`${vms.filter(v=>v.statut==='running').length} VMs RUNNING`} color={C.green}/>
        </div>
      </div>

      <div style={{ display:'grid', gridTemplateColumns: sel ? '1fr 1.4fr' : '1fr', gap:16 }}>
        <div style={{ display:'flex', flexDirection:'column', gap:12 }}>

          {/* ← SEUL CHANGEMENT : 10→11, fontWeight:700 ajouté */}
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
                    {/* ← SEUL CHANGEMENT : 14→15 */}
                    <div style={{ fontSize:15, fontWeight:700, color:C.text }}>{nom}</div>
                    {/* ← SEUL CHANGEMENT : 11→12 */}
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
                    {/* ← SEUL CHANGEMENT : 11→12 */}
                    {!tempOk && <span style={{ fontSize:12, color:C.orange }}>🌡 {n.cpu_temp_max_c}°C</span>}
                    {!(n.smart_ok??true) && <span style={{ fontSize:12, color:C.red }}>⚠ DISK</span>}
                    {!(n.corosync_ok??true) && <span style={{ fontSize:12, color:C.red }}>⚠ QUORUM</span>}
                    <Dot on={isOnline}/>
                    {/* ← SEUL CHANGEMENT : 11→12 */}
                    <span style={{ fontSize:12, color:isOnline?C.green:C.red, fontWeight:700 }}>{isOnline?'Online':'Offline'}</span>
                  </div>
                </div>
                <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr 1fr', gap:8 }}>
                  <div>
                    {/* ← SEUL CHANGEMENT : 10→11 */}
                    <div style={{ fontSize:11, color:C.muted, marginBottom:3 }}>CPU</div>
                    <Bar val={n.cpu_pct??0} color={cpuColor}/>
                    {/* ← SEUL CHANGEMENT : 11→12 */}
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
                {/* Indicateurs avancés — IDENTIQUE à l'original */}
                <div style={{ display:'flex', gap:12, marginTop:10, flexWrap:'wrap' }}>
                  {(n.swap_pct??0)>0 && <span style={{ fontSize:11, color:(n.swap_pct??0)>50?C.orange:C.muted }}>Swap {n.swap_pct??0}%</span>}
                  {(n.cpu_iowait_pct??0)>0 && <span style={{ fontSize:11, color:(n.cpu_iowait_pct??0)>15?C.orange:C.muted }}>I/O wait {n.cpu_iowait_pct??0}%</span>}
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

        {sel && (selectedNode || selectedVm) && (
          <Card style={{ padding:'16px 20px', position:'sticky', top:20, maxHeight:'85vh', overflowY:'auto' }}>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginBottom:12 }}>
              <div>
                {/* ← SEUL CHANGEMENT : 15→16, 700→800 */}
                <div style={{ fontSize:16, fontWeight:800, color:C.text }}>
                  {selectedNode ? (selectedNode.nom||selectedNode.node) : (selectedVm.nom||selectedVm.name||`VM-${selectedVm.vmid}`)}
                </div>
                {/* ← SEUL CHANGEMENT : 11→12 */}
                <div style={{ fontSize:12, color:C.muted }}>
                  {selectedNode ? 'Proxmox Hypervisor — All metrics' : `VM ${selectedVm.vmid} — All metrics`}
                </div>
              </div>
              <button onClick={()=>setSel(null)}
                style={{ background:'transparent', border:`1px solid ${C.border}`, color:C.muted, borderRadius:6, padding:'4px 10px', cursor:'pointer', fontSize:12 }}>
                ✕ Close
              </button>
            </div>
            {selectedNode && <NodeDetail n={selectedNode} vms={vms}/>}
            {selectedVm   && <VmDetail   v={selectedVm}/>}
          </Card>
        )}
      </div>
    </div>
  )
}