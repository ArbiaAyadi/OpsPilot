import { useState, useCallback } from 'react'
import { C } from '../utils/colors'
import { RuleRow } from '../components/RuleRow'

export function PageMonitoringRules({ reglesDynamiques = [] }) {
  const [regenerating, setRegenerating] = useState(false)
  const [regenerated,  setRegenerated]  = useState(false)
  // openRules : Set des cles de regles actuellement ouvertes.
  // Gere ici (parent) et pas dans RuleRow pour survivre aux re-renders
  // causes par les cycles WebSocket (toutes les 60s) qui fermeraient
  // les panneaux si l'etat etait local au composant enfant.
  const [openRules, setOpenRules] = useState(new Set())

  const toggleRule = useCallback((key) => {
    setOpenRules(prev => {
      const next = new Set(prev)
      if (next.has(key)) next.delete(key)
      else next.add(key)
      return next
    })
  }, [])

  const reglesFallback = [
    // CRITICAL
    { metric:'server.cpu_pct',       op:'>',  seuil:'80%',   dur:'5min',  sev:'CRITICAL',   src:'Proxmox VE Docs',             desc:"Sustained CPU > 80% causes VM contention and latency spikes. Migrate VMs or add resources.",                                     cmd:"pvesh get /nodes/{node}/status | grep cpu\nqm list" },
    { metric:'server.ram_pct',       op:'>',  seuil:'85%',   dur:'5min',  sev:'CRITICAL',   src:'Linux/Proxmox Best Practices', desc:"RAM > 85% triggers swap and risks OOM kills. Proxmox will start killing processes.",                                                cmd:"pvesh get /nodes/{node}/status | grep mem\nfree -h" },
    { metric:'server.disk_pct',      op:'>',  seuil:'90%',   dur:'—',     sev:'CRITICAL',   src:'Proxmox Storage Docs',         desc:"Disk > 90% on LVM-thin: new writes fail silently, VMs may corrupt. Immediate action required.",                                    cmd:"pvesm status\ndf -h\nlvs --units g" },
    { metric:'vm.statut',            op:'=',  seuil:'down',  dur:'—',     sev:'CRITICAL',   src:'Proxmox HA Docs',              desc:"VM stopped unexpectedly. Could indicate OOM kill, hardware fault, or HA fencing.",                                                  cmd:"qm status {vmid}\npvecm status\njournalctl -u pve-ha-lrm" },
    { metric:'cluster.quorum',       op:'=',  seuil:'lost',  dur:'—',     sev:'CRITICAL',   src:'Proxmox Cluster Docs',         desc:"Quorum lost -- Proxmox will fence nodes and stop VMs to prevent split-brain data corruption.",                                       cmd:"pvecm status\ncorosync-cfgtool -s\njournalctl -u corosync" },
    { metric:'disk.smart_uncorr',    op:'>',  seuil:'0',     dur:'—',     sev:'CRITICAL',   src:'SMART Monitoring',             desc:"SMART uncorrectable errors = data loss occurred or imminent. EMERGENCY: backup immediately and replace disk.",                       cmd:"smartctl -H /dev/sda\nsmartctl -l selftest /dev/sda" },
    { metric:'disk.smart_realloc',   op:'>',  seuil:'0',     dur:'—',     sev:'CRITICAL',   src:'SMART Monitoring',             desc:"Reallocated sectors = disk physically degraded, moving data from bad sectors. Plan immediate replacement.",                          cmd:"smartctl -a /dev/sda | grep -i reallocated" },
    { metric:'node.swap_pct',        op:'>',  seuil:'80%',   dur:'5min',  sev:'CRITICAL',   src:'Linux Memory Management',      desc:"Swap > 80% means RAM is saturated. System using slow disk as RAM -- performance is catastrophic.",                                   cmd:"free -h\nswapon --show\nps aux --sort=-%mem | head -10" },
    { metric:'node.cpu_iowait_pct',  op:'>',  seuil:'30%',   dur:'5min',  sev:'CRITICAL',   src:'Linux Performance Analysis',   desc:"CPU I/O wait > 30%: CPU is blocked waiting for disk. Storage is the bottleneck, not CPU.",                                         cmd:"iostat -x 1 5\niotop -o" },
    { metric:'node.disk_latency_ms', op:'>',  seuil:'50ms',  dur:'—',     sev:'CRITICAL',   src:'Storage Best Practices',       desc:"Disk latency > 50ms: extremely slow storage. SSD may be failing or ZFS pool degraded.",                                            cmd:"zpool status\niostat -x 1 5" },
    { metric:'node.cpu_temp_c',      op:'>',  seuil:'85C',   dur:'—',     sev:'CRITICAL',   src:'Intel/AMD Thermal Specs',      desc:"CPU temperature > 85C: automatic throttling engaged. CPU running at reduced speed. Check cooling immediately.",                     cmd:"sensors | grep -i temp" },
    // HIGH
    { metric:'server.cpu_pct',       op:'>',  seuil:'65%',   dur:'15min', sev:'HIGH',       src:'Capacity Planning',            desc:"Early CPU warning: plan VM migration before reaching critical threshold.",                                                           cmd:"pvesh get /nodes/{node}/status" },
    { metric:'server.ram_pct',       op:'>',  seuil:'75%',   dur:'15min', sev:'HIGH',       src:'Proxmox Memory Management',    desc:"Memory pressure building. Review VM allocations before saturation.",                                                                cmd:"pvesh get /nodes/{node}/status" },
    { metric:'server.disk_pct',      op:'>',  seuil:'80%',   dur:'—',     sev:'HIGH',       src:'Proxmox Storage Docs',         desc:"Storage above 80%: clean snapshots or expand pool before hitting critical threshold.",                                              cmd:"pvesm status" },
    { metric:'vm.cpu_pct',           op:'>',  seuil:'80%',   dur:'5min',  sev:'HIGH',       src:'Proxmox VE Docs',              desc:"VM CPU > 80% sustained: check for overcommit on hypervisor. May need vCPU increase.",                                              cmd:"qm monitor {vmid}" },
    { metric:'vm.ram_pct',           op:'>',  seuil:'85%',   dur:'5min',  sev:'HIGH',       src:'Proxmox Memory Management',    desc:"VM RAM > 85%: risk of VM-level swap and application performance degradation.",                                                      cmd:"qm monitor {vmid}" },
    { metric:'node.swap_pct',        op:'>',  seuil:'50%',   dur:'10min', sev:'HIGH',       src:'Linux Best Practices',         desc:"Swap > 50%: memory pressure building. Identify top consumers before saturation.",                                                   cmd:"ps aux --sort=-%mem | head -15" },
    { metric:'node.cpu_iowait_pct',  op:'>',  seuil:'15%',   dur:'10min', sev:'HIGH',       src:'Linux Performance Analysis',   desc:"CPU I/O wait > 15%: disk pressure detected. Check storage throughput.",                                                             cmd:"iotop -o" },
    { metric:'node.disk_latency_ms', op:'>',  seuil:'10ms',  dur:'5min',  sev:'HIGH',       src:'Storage Best Practices',       desc:"Disk latency > 10ms: storage degradation. Normal SSD latency is < 1ms.",                                                           cmd:"iostat -x 1 5" },
    { metric:'node.cpu_temp_c',      op:'>',  seuil:'75C',   dur:'10min', sev:'HIGH',       src:'Intel/AMD Thermal Specs',       desc:"CPU approaching thermal limit. Verify fan operation and airflow.",                                                                  cmd:"sensors" },
    { metric:'zfs.arc_hit_rate',     op:'<',  seuil:'70%',   dur:'30min', sev:'HIGH',       src:'ZFS Best Practices',           desc:"ZFS ARC hit rate < 70%: most I/O going to physical disk. Adding RAM will dramatically improve performance.",                        cmd:"arc_summary || cat /proc/spl/kstat/zfs/arcstats | grep -E hits" },
    { metric:'net.errors_per_sec',   op:'>',  seuil:'10/s',  dur:'5min',  sev:'HIGH',       src:'Network Diagnostics',          desc:"Network interface errors > 10/s: hardware issue. Check cable, switch port, or NIC driver.",                                         cmd:"ip -s link show\nethtool eth0" },
    // MONITORING
    { metric:'lstm.score',           op:'>',  seuil:'0.8',   dur:'—',     sev:'MONITORING', src:'OpsPilot AI Engine',           desc:"LSTM AI anomaly score > 0.8: highly abnormal cluster behavior detected. Investigate all metrics.",                                  cmd:"" },
    { metric:'lstm.score',           op:'>',  seuil:'0.5',   dur:'—',     sev:'MONITORING', src:'OpsPilot AI Engine',           desc:"AI anomaly score > 0.5: suspicious pattern detected. Enhanced monitoring activated automatically.",                                  cmd:"" },
    { metric:'isolation_forest',     op:'>',  seuil:'0.7',   dur:'—',     sev:'MONITORING', src:'OpsPilot AI Engine',           desc:"Isolation Forest score > 0.7: statistical anomaly detected immediately on startup.",                                                 cmd:"" },
    { metric:'node.load_avg_15m',    op:'>',  seuil:'cores', dur:'15min', sev:'MONITORING', src:'Linux Performance',            desc:"Load average > number of CPU cores sustained 15min: system overloaded.",                                                             cmd:"uptime\nnproc" },
    { metric:'node.fd_used_pct',     op:'>',  seuil:'80%',   dur:'5min',  sev:'MONITORING', src:'Linux System Limits',          desc:"File descriptors > 80% of max: risk of too many open files errors stopping services.",                                              cmd:"cat /proc/sys/fs/file-nr" },
  ]

  // Le LLM (rules_engine.py) genere les regles avec des severites en
  // francais ("CRITIQUE", "IMPORTANT", "SURVEILLANCE") car c'est la langue
  // du prompt Proxmox docs. Le frontend filtre en anglais (CRITICAL/HIGH/
  // MONITORING). Sans cette table de conversion, les 8 regles generees
  // n'appartenaient a AUCUN groupe de severite et n'etaient jamais affichees,
  // meme si elles existaient bien dans reglesDynamiques.
  const SEV_MAP = {
    CRITIQUE:     'CRITICAL',
    CRITICAL:     'CRITICAL',
    IMPORTANT:    'HIGH',
    HIGH:         'HIGH',
    SURVEILLANCE: 'MONITORING',
    MONITORING:   'MONITORING',
  }
  const normSev = (s) => SEV_MAP[String(s||'').toUpperCase()] || 'MONITORING'

  const regles = reglesDynamiques.length > 0
    ? reglesDynamiques.map(r => ({
        metric: r.metric, op: r.operateur||'>', seuil: String(r.seuil),
        dur: r.duree_min > 0 ? `${r.duree_min}min` : '—',
        sev: normSev(r.severite), src: r.source||'AI', desc: r.description, cmd: r.action||'',
      }))
    : reglesFallback

  const isAI  = reglesDynamiques.length > 0
  const crit  = regles.filter(r => r.sev === 'CRITICAL')
  const high  = regles.filter(r => r.sev === 'HIGH')
  const mon   = regles.filter(r => r.sev === 'MONITORING')

  const regenerer = async () => {
    setRegenerating(true)
    try {
      await fetch('/api/regles/regenerer', { method: 'POST' })
      setRegenerated(true); setTimeout(() => setRegenerated(false), 3000)
    } catch {}
    setRegenerating(false)
  }

  const Group = ({ title, color, rules }) => {
    if (!rules.length) return null
    return (
      <div style={{ display:'flex', flexDirection:'column', gap:4 }}>
        <div style={{ display:'flex', alignItems:'center', gap:8, padding:'8px 0 4px' }}>
          <div style={{ width:9, height:9, borderRadius:'50%', background:color, flexShrink:0 }}/>
          <span style={{ fontSize:11, fontWeight:800, letterSpacing:'0.12em', color, fontFamily:'JetBrains Mono,monospace' }}>
            {title} — {rules.length} RULE{rules.length>1?'S':''}
          </span>
          <div style={{ flex:1, height:1, background:color+'25' }}/>
        </div>
        {rules.map((r, i) => {
          const key = `${title}_${r.metric}_${i}`
          return <RuleRow key={key} r={r} color={color} isAI={isAI} isOpen={openRules.has(key)} onToggle={() => toggleRule(key)}/>
        })}
      </div>
    )
  }

  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'flex-start' }}>
        <div>
          {/* ← SEUL CHANGEMENT : 18→22, 700→800 */}
          <h2 style={{ fontSize:22, fontWeight:800, color:C.text, marginBottom:6 }}>Monitoring Rules</h2>
          {/* ← SEUL CHANGEMENT : 12→13 */}
          <div style={{ fontSize:13, color:C.sub }}>
            {isAI ? `${regles.length} AI-generated rules · click to expand` : `${regles.length} rules from Proxmox official docs · click to expand`}
          </div>
        </div>
        <div style={{ display:'flex', gap:10, alignItems:'center' }}>
          {/* ← SEUL CHANGEMENT : 11→12 */}
          <div style={{ display:'flex', gap:10, fontSize:12, fontFamily:'JetBrains Mono,monospace', fontWeight:600 }}>
            <span style={{ color:'#ef4444' }}>{crit.length} critical</span>
            <span style={{ color:'#f97316' }}>{high.length} high</span>
            <span style={{ color:'#eab308' }}>{mon.length} monitoring</span>
          </div>
          {/* ← SEUL CHANGEMENT : 11→12 */}
          <button onClick={regenerer} disabled={regenerating}
            style={{ display:'flex', alignItems:'center', gap:6, padding:'7px 16px', borderRadius:7, border:`1px solid ${C.borderHi}`, background:regenerated?'#22c55e18':C.card, color:regenerated?'#22c55e':C.sub, cursor:'pointer', fontSize:12, fontFamily:'JetBrains Mono,monospace', fontWeight:600, transition:'all 0.2s' }}
            onMouseEnter={e=>{ if(!regenerating) e.currentTarget.style.borderColor='#a855f7' }}
            onMouseLeave={e=>{ e.currentTarget.style.borderColor=C.borderHi }}
          >
            <span style={{ animation:regenerating?'spin 1s linear infinite':'none', display:'inline-block' }}>↺</span>
            {regenerating?'Generating...':regenerated?'✓ Done':'Regenerate with AI'}
          </button>
        </div>
      </div>

      {/* ← SEUL CHANGEMENT : 12→13 pour le texte */}
      <div style={{ background:'#3b82f608', border:'1px solid #3b82f620', borderRadius:8, padding:'9px 14px', display:'flex', gap:10, alignItems:'center' }}>
        <span style={{ color:'#3b82f6', fontSize:15 }}>ℹ</span>
        <span style={{ fontSize:13, color:C.sub, lineHeight:1.6 }}>
          Rules define <strong style={{ color:C.text }}>when OpsPilot triggers an alert</strong>. Applied continuously to live metrics.
          <strong style={{ color:C.text }}> Click any rule</strong> to see description and diagnostic commands.
          <strong style={{ color:C.text }}> Regenerate</strong> to adapt thresholds to your cluster baseline.
        </span>
      </div>

      <Group title="CRITICAL"   color="#ef4444" rules={crit}/>
      <Group title="HIGH"       color="#f97316" rules={high}/>
      <Group title="MONITORING" color="#eab308" rules={mon}/>
    </div>
  )
}