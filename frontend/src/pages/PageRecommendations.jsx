/**
 * OpsPilot — Page Recommendations
 * Affiche les actions correctives avec solutions spécifiques par type de problème,
 * valeurs cibles exactes, et commandes Proxmox VE officielles.
 */
import { useState } from 'react'
import { C } from '../utils/colors'
import { severityColor, normalizeSeverity } from '../styles/theme'
import { Card } from '../components/Card'
import { Chip } from '../components/Common'

// ─── Librairie de solutions optimales par type de problème ───────────────────
// Basée sur : pve.proxmox.com/pve-docs/pve-admin-guide.html
//             pve.proxmox.com/wiki/Dynamic_Memory_Management
//             forum.proxmox.com + linuxconfig.org/reclaiming-proxmox-storage
// ─────────────────────────────────────────────────────────────────────────────
const SOLUTIONS = {
  ram: {
    icon: '▣',
    color: '#f97316',
    label: 'RAM Pressure',
    thresholds: { warning: 75, critical: 85, target: 70 },
    // Commande principale : identifier les consommateurs RAM sur hyperviseur Proxmox
    cmd: `ps aux --sort=-%mem | head -15`,
    cmdLabel: 'Top RAM consumers on the hypervisor node',
    cmdExpl: 'Lists the 15 processes using the most RAM. Look at the %MEM column — identify qemu-system processes (VMs) and pveproxy/corosync (PVE services). If a qemu process dominates, reduce that VM\'s maxmem via the GUI.',
    steps: [
      'Run the command above — note the VM process consuming the most RAM',
      'Check per-VM balloon status: pvesh get /nodes/{node}/qemu/{vmid}/status/current',
      'Enable ballooning on RAM-heavy VMs: qm set {vmid} --balloon {min_mb} (e.g. --balloon 512 for a 2GB VM)',
      'Verify KSM is active: cat /sys/kernel/mm/ksm/run  (should return 1)',
      'If KSM is off: echo 1 > /sys/kernel/mm/ksm/run',
    ],
    longTerm: 'Set memory balloon minimum for all VMs (PVE GUI → VM → Hardware → Memory → Ballooning). Proxmox activates KSM deduplication at 80% host RAM. Target: host RAM < 70% after ballooning.',
    ref: 'pve.proxmox.com/wiki/Dynamic_Memory_Management',
  },
  disk: {
    icon: '◉',
    color: '#ef4444',
    label: 'Disk Usage',
    thresholds: { warning: 80, critical: 90, target: 75 },
    cmd: `du -sh /var/lib/vz/dump/* 2>/dev/null | sort -rh | head -10
df -h | grep -v tmpfs
ls -lh /var/log/pve/ | sort -k5 -rh | head -10`,
    cmdLabel: 'Identify disk space consumers: backups, filesystems, PVE logs',
    cmdExpl: 'First block shows backup sizes in /var/lib/vz/dump/ (largest first). Second shows filesystem usage. Third shows oversized PVE log files. Backups and old kernels are the most common causes.',
    steps: [
      'Check backup space: ls -lh /var/lib/vz/dump/ — delete old backups: rm /var/lib/vz/dump/vzdump-*.vma.zst',
      'Clean apt cache (safe, always): apt clean && apt autoremove -y',
      'Truncate oversized PVE logs: journalctl --vacuum-size=500M',
      'Remove old kernels: pveversion -v | grep pve-kernel — then: apt remove --purge {old-kernel}',
      'Check storage pools: pvesm status — identify pools above 85%',
    ],
    longTerm: 'Configure backup retention in PVE GUI → Datacenter → Backup → Edit → Keep Last N. Set to 2-3 max. Add logrotate rule for /var/log/pve/ to cap at 100MB. Target: disk < 75%.',
    ref: 'linuxconfig.org/reclaiming-proxmox-storage-disk-space-via-linux-cli',
  },
  cpu: {
    icon: '◈',
    color: '#f59e0b',
    label: 'CPU Saturation',
    thresholds: { warning: 80, critical: 90, target: 75 },
    cmd: `top -b -n1 | head -25`,
    cmdLabel: 'Real-time CPU usage snapshot with process breakdown',
    cmdExpl: 'Look at the %CPU column. qemu-system processes = running VMs. If one VM dominates, reduce its vCPU count or enable CPU pinning. iowait% > 10% means disk bottleneck, not CPU.',
    steps: [
      'Run top — note VM process with highest %CPU',
      'Check VM CPU config: qm config {vmid} | grep -E "cores|sockets|cpu"',
      'Reduce vCPU if overprovisioned: qm set {vmid} --cores {n}  (max = physical cores / num_vms)',
      'Enable CPU pinning for critical VMs: qm set {vmid} --affinity 0-3  (pin to cores 0-3)',
      'Check iowait: iostat -x 1 3 — if %iowait > 10%, the bottleneck is storage, not CPU',
    ],
    longTerm: 'Never exceed 1.5× physical cores total vCPU. Use CPU limits in PVE: qm set {vmid} --cpulimit 1.0 to cap a single VM at 1 full core. Review vCPU allocation quarterly.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#_cpu_resources',
  },
  swap: {
    icon: '⬡',
    color: '#8b5cf6',
    label: 'Swap Usage',
    thresholds: { warning: 20, critical: 50, target: 0 },
    cmd: `swapon --show
free -h
dmesg | grep -i "out of memory" | tail -5`,
    cmdLabel: 'Show swap usage, free memory, and OOM killer history',
    cmdExpl: 'Swap usage on Proxmox hypervisor means RAM is genuinely exhausted. OOM killer entries in dmesg confirm it killed processes. Swap > 20% = immediate RAM overcommit issue.',
    steps: [
      'Identify what triggered swap: vmstat 1 5 — if si/so > 0, swap is actively used',
      'Check total VM allocation vs physical: grep MemTotal /proc/meminfo',
      'Reduce VM maxmem to free host RAM: qm set {vmid} --memory {mb}',
      'If ZFS is installed: limit ARC to free RAM — echo {bytes} > /sys/module/zfs/parameters/zfs_arc_max',
      'Emergency: stop a non-critical VM — qm stop {vmid}',
    ],
    longTerm: 'Proxmox best practice: host RAM should never be fully committed. Keep 2-4 GB free for PVE OS + Corosync. Swap on hypervisor = emergency buffer only, never normal operation. Upgrade RAM if swap is regularly used.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#sysadmin_zfs_limit_memory_usage',
  },
  iowait: {
    icon: '◬',
    color: '#06b6d4',
    label: 'I/O Wait',
    thresholds: { warning: 10, critical: 20, target: 5 },
    cmd: `iostat -x 1 3
iotop -b -n 1 | head -20`,
    cmdLabel: 'Per-device I/O stats and top disk-consuming processes',
    cmdExpl: '%util > 80% on a device = saturated disk. r_await/w_await > 20ms = high latency. The process list shows which VM or service is the I/O source.',
    steps: [
      'Run iostat — check %util and await columns per device',
      'If %util > 80%: check which VM is causing it with iotop',
      'Add I/O throttle to the VM: qm set {vmid} --ide0 {storage}:{disk},mbps_rd=100,mbps_wr=50',
      'Check for ZFS scrub running: zpool status — scrub causes high I/O, schedule for off-peak',
      'Review backup schedules: vzdump running during peak hours saturates disk',
    ],
    longTerm: 'Set I/O throttle (mbps_rd/mbps_wr) on all VMs in PVE GUI → VM → Hardware → Disk → Edit → Bandwidth. Schedule vzdump backups at night. Target: iowait < 5%, await < 10ms.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#_disk_image_format',
  },
  temp: {
    icon: '◉',
    color: '#ef4444',
    label: 'CPU Temperature',
    thresholds: { warning: 75, critical: 85, target: 70 },
    cmd: `sensors
ipmitool sdr type Temperature 2>/dev/null || echo "IPMI not available"
cat /sys/class/thermal/thermal_zone*/temp`,
    cmdLabel: 'CPU temperature from all available sensors',
    cmdExpl: 'Shows all thermal zones. Divide /sys values by 1000 to get °C. Above 75°C = throttling risk. Above 85°C = automatic CPU frequency reduction which causes CPU% to appear high.',
    steps: [
      'Run sensors — note which core is hottest',
      'Check CPU throttling: dmesg | grep -i "throttl" | tail -10',
      'Verify fan operation: ipmitool sdr type Fan  (if available)',
      'Reduce VM CPU load temporarily: qm set {vmid} --cpulimit 0.5',
      'Check chassis airflow: ensure no blocked vents (physical check)',
    ],
    longTerm: 'Install lm-sensors: apt install lm-sensors && sensors-detect. Configure thermal monitoring in Proxmox notifications. Physical: clean dust filters, verify thermal paste on CPU if node is > 3 years old. Target: < 70°C under load.',
    ref: 'pve.proxmox.com/wiki/Proxmox_VE_Administration_Guide',
  },
  vm_down: {
    icon: '▶',
    color: '#ef4444',
    label: 'VM Not Running',
    thresholds: {},
    cmd: `qm list
qm status {vmid}
journalctl -u qmeventd --since "1 hour ago" | tail -30`,
    cmdLabel: 'List all VMs with status and check recent VM events',
    cmdExpl: 'qm list shows all VMs and their state. qmeventd logs show why a VM stopped (OOM kill, disk full, migration failure). Identify the stopped VM and the cause before restarting.',
    steps: [
      'Run qm list — identify stopped VMs',
      'Check why it stopped: qm status {vmid} --verbose',
      'Check OOM kills: dmesg | grep -i "oom" | tail -10',
      'If disk full caused stop: free space first (see Disk section), then restart',
      'Restart VM: qm start {vmid}',
    ],
    longTerm: 'Enable HA for critical VMs: PVE GUI → Datacenter → HA → Add. Set VM startup order: PVE GUI → Node → VM → Options → Start/Shutdown order. Configure Watchdog for automatic VM restart on failure.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#chapter_ha',
  },
  quorum: {
    icon: '⚙',
    color: '#ef4444',
    label: 'Cluster Quorum',
    thresholds: {},
    cmd: `pvecm status
corosync-cfgtool -s
systemctl status corosync`,
    cmdLabel: 'Cluster quorum status and Corosync ring health',
    cmdExpl: 'pvecm status shows Quorate: Yes/No and number of votes. Corosync ring shows network connectivity between nodes. Lost quorum = cluster is read-only, VMs cannot be started or migrated.',
    steps: [
      'Run pvecm status — check "Quorate" field and vote count',
      'Ping the other node: ping 192.168.138.101  (from pve1)',
      'Check Corosync network: corosync-cfgtool -s — both rings should show "no faults"',
      'Restart Corosync if stuck: systemctl restart corosync',
      'If node is genuinely offline: pvecm expected 1  (DANGER: only if node is confirmed dead)',
    ],
    longTerm: 'For a 2-node cluster, add a QDevice (Quorum Device) to avoid split-brain: pvecm qdevice setup {qdevice-ip}. A Raspberry Pi or small VM on a third host can serve as QDevice. This eliminates the 2-node quorum vulnerability.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#chapter_pvecm',
  },
  network: {
    icon: '⟳',
    color: '#f97316',
    label: 'Network Errors / Packet Drops',
    thresholds: { warning: 1, critical: 10, target: 0 },
    cmd: `ip -s link show
ethtool eth0
cat /proc/net/dev`,
    cmdLabel: 'Network interface stats — errors and drops per interface',
    cmdExpl: 'RX/TX errors > 0 indicate hardware or cable issues. Drops > 0 indicate buffer saturation or CPU overload. ethtool shows duplex/speed mismatch which causes errors on gigabit links.',
    steps: [
      'Run ip -s link — note RX/TX errors and drops per interface',
      'Check duplex/speed: ethtool eth0 | grep -E "Speed|Duplex"',
      'Verify cable and switch port: replace cable if errors persist',
      'Check CPU overload causing drops: top — if CPU > 80%, network buffers overflow',
      'Increase ring buffer: ethtool -G eth0 rx 4096 tx 4096',
    ],
    longTerm: 'Set network card to auto-negotiate: ethtool -s eth0 autoneg on. Configure jumbo frames if using NFS/iSCSI storage: ip link set eth0 mtu 9000. Monitor with: watch -n1 "ip -s link show eth0". Target: 0 errors, 0 drops.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#sysadmin_network_configuration',
  },
  smart: {
    icon: '💾',
    color: '#ef4444',
    label: 'Disk SMART Failure',
    thresholds: {},
    cmd: `smartctl -a /dev/sda
smartctl -H /dev/sda
lsblk -d -o NAME,SIZE,MODEL,SERIAL`,
    cmdLabel: 'Full SMART report and disk health for /dev/sda',
    cmdExpl: 'SMART overall-health: PASSED = OK, FAILED = replace immediately. Reallocated_Sector_Ct > 0 = bad sectors found and remapped. Current_Pending_Sector > 0 = unstable sectors. Uncorrectable_Error > 0 = URGENT: data loss risk.',
    steps: [
      'Run smartctl -H /dev/sda — check overall-health status',
      'Check critical attributes: smartctl -a /dev/sda | grep -E "Reallocated|Pending|Uncorrectable"',
      'If Reallocated_Sector_Ct > 0: schedule disk replacement immediately',
      'Backup all VMs NOW: vzdump --all --compress zstd --storage local',
      'Monitor disk temp: smartctl -a /dev/sda | grep Temperature — should be < 50°C',
    ],
    longTerm: 'Install smartmontools: apt install smartmontools. Enable automatic SMART tests: smartd with /etc/smartd.conf. Schedule weekly short tests and monthly long tests. Replace any disk with Reallocated_Sector_Ct > 0 — it will fail. Target: SMART PASSED, 0 reallocated sectors.',
    ref: 'pve.proxmox.com/wiki/SMART',
  },
  zfs: {
    icon: '◈',
    color: '#f97316',
    label: 'ZFS ARC Cache',
    thresholds: { warning: 80, critical: 60, target: 90 },
    cmd: `arc_summary
zpool status
zpool iostat -v 1 3`,
    cmdLabel: 'ZFS ARC cache hit rate and pool health',
    cmdExpl: 'ARC hit rate < 80% means most I/O goes to physical disk — performance degrades significantly. zpool status shows DEGRADED/FAULTED pools. iostat shows read/write throughput per vdev. A healthy pool shows ONLINE for all disks.',
    steps: [
      'Run arc_summary — check ARC hit rate and size',
      'If hit rate < 80%: increase ARC max size',
      'Set ARC max to 50% of RAM: echo {bytes} > /sys/module/zfs/parameters/zfs_arc_max',
      'Check pool health: zpool status — all vdevs should be ONLINE',
      'If pool DEGRADED: check failed disk with zpool status -v',
    ],
    longTerm: 'Make ARC limit permanent: add options zfs zfs_arc_max={bytes} to /etc/modprobe.d/zfs.conf. Enable ZFS auto-scrub monthly: zpool set autoreplace=on {pool}. Monitor with: zpool iostat 5. Target: ARC hit rate > 90%, all vdevs ONLINE.',
    ref: 'pve.proxmox.com/pve-docs/pve-admin-guide.html#sysadmin_zfs',
  },
}

// Détecter le type de problème depuis le message/titre
function detecterType(s) {
  const text = ((s.title || '') + ' ' + (s.description || '') + ' ' + (s.target || '')).toLowerCase()
  if (text.includes('quorum') || text.includes('corosync'))                    return 'quorum'
  if (text.includes('smart') || text.includes('reallocated') || text.includes('sector')) return 'smart'
  if (text.includes('zfs') || text.includes('arc') || text.includes('zpool')) return 'zfs'
  if (text.includes('temperature') || text.includes('temp'))                   return 'temp'
  if (text.includes('network') || text.includes('packet') || text.includes('drop') || text.includes('net error')) return 'network'
  if (text.includes('iowait') || text.includes('i/o wait'))                   return 'iowait'
  if (text.includes('swap'))                                                    return 'swap'
  if (text.includes('disk') || text.includes('storage'))                       return 'disk'
  if (text.includes('cpu') && !text.includes('iowait'))                        return 'cpu'
  if (text.includes('ram') || text.includes('memory') || text.includes('mem')) return 'ram'
  if (text.includes('vm') && (text.includes('stop') || text.includes('down') || text.includes('not running'))) return 'vm_down'
  const cible = (s.target || '').toLowerCase()
  if (cible.includes('ram') || cible.includes('mem'))  return 'ram'
  if (cible.includes('disk'))                          return 'disk'
  if (cible.includes('cpu'))                           return 'cpu'
  if (cible.includes('swap'))                          return 'swap'
  if (cible.includes('network') || cible.includes('net')) return 'network'
  if (cible.includes('vm'))                            return 'vm_down'
  return 'ram'
}

// Extraire la valeur actuelle depuis le message LLM ou anomalie
function extraireValeur(s) {
  const text = s.description || s.title || ''
  // Cherche patterns : "78.5%", "at 78.5", "85.6%"
  const match = text.match(/(\d{1,3}\.?\d?)\s*%/)
  return match ? parseFloat(match[1]) : null
}

// Extraire le nœud cible
function extraireNode(s) {
  const text = (s.title || '') + ' ' + (s.description || '')
  const match = text.match(/\b(pve\d+|linux-vm\d+)\b/i)
  return match ? match[1] : (s.target || 'cluster')
}

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

function RecoCard({ s }) {
  const [expanded, setExpanded] = useState(true)
  const sev     = normalizeSeverity(s.severity)
  const sevColor = severityColor(s.severity)
  const type    = detecterType(s)
  const sol     = SOLUTIONS[type] || SOLUTIONS.ram
  const valeur  = extraireValeur(s)
  const node    = extraireNode(s)

  // Construire le message "Problem" avec valeur exacte si disponible
  const problemMsg = valeur && sol.thresholds.warning
    ? `${node} ${sol.label} at ${valeur}% — exceeds ${valeur >= sol.thresholds.critical ? sol.thresholds.critical + '% critical' : sol.thresholds.warning + '% warning'} threshold — target: below ${sol.thresholds.target}%`
    : `${node}: ${s.title || sol.label}`

  return (
    <Card glow={sol.color} style={{ padding: 0, overflow: 'hidden' }}>
      <div style={{ display: 'flex' }}>
        {/* Barre colorée gauche */}
        <div style={{ width: 4, background: sol.color, flexShrink: 0, borderRadius: '8px 0 0 8px' }} />

        <div style={{ flex: 1, padding: '16px 20px' }}>
          {/* Header */}
          <div
            onClick={() => setExpanded(v => !v)}
            style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', cursor: 'pointer', marginBottom: expanded ? 14 : 0 }}
          >
            <div style={{ display: 'flex', alignItems: 'center', gap: 10, flex: 1, marginRight: 12 }}>
              <span style={{ fontSize: 16, color: sol.color }}>{sol.icon}</span>
              <div>
                <div style={{ fontSize: 15, fontWeight: 700, color: C.text, lineHeight: 1.3 }}>{s.title}</div>
                <div style={{ fontSize: 11, color: C.muted, marginTop: 2, fontFamily: 'JetBrains Mono, monospace' }}>{sol.label} · {node}</div>
              </div>
            </div>
            <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexShrink: 0 }}>
              <Chip label={sev} color={sevColor} />
              <Chip label={s.status || 'OPEN'} color={s.status === 'RESOLVED' ? C.green : C.yellow} />
              <span style={{ color: C.muted, fontSize: 11, marginLeft: 4 }}>{expanded ? '▲' : '▼'}</span>
            </div>
          </div>

          {expanded && (
            <>
              {/* Bloc valeur exacte + cibles */}
              <div style={{ marginBottom: 14, padding: '10px 14px', background: sol.color + '14', borderRadius: 7, border: `1px solid ${sol.color}35` }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: sol.color, letterSpacing: '0.08em', marginBottom: 5, fontFamily: 'JetBrains Mono, monospace' }}>DETECTED ISSUE</div>
                <div style={{ fontSize: 12, color: C.text, lineHeight: 1.5 }}>{problemMsg}</div>
                {sol.thresholds.warning && (
                  <div style={{ display: 'flex', gap: 16, marginTop: 8 }}>
                    <span style={{ fontSize: 10, color: C.orange, fontFamily: 'JetBrains Mono, monospace' }}>⚠ WARNING ≥ {sol.thresholds.warning}%</span>
                    <span style={{ fontSize: 10, color: C.red, fontFamily: 'JetBrains Mono, monospace' }}>✕ CRITICAL ≥ {sol.thresholds.critical}%</span>
                    <span style={{ fontSize: 10, color: C.green, fontFamily: 'JetBrains Mono, monospace' }}>✓ TARGET &lt; {sol.thresholds.target}%</span>
                  </div>
                )}
              </div>

              {/* Métriques complètes niveaux 1+2+3 si disponibles */}
              {(s.swap_pct !== null || s.cpu_iowait_pct !== null || s.cpu_temp_max_c !== null || s.zfs_available || s.corosync_ok !== null) && (
                <div style={{ marginBottom: 14, display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 6 }}>
                  {/* Niveau 2 — Swap */}
                  {s.swap_pct !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>SWAP</div>
                      <div style={{ fontSize: 12, color: s.swap_pct > 50 ? C.red : s.swap_pct > 20 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.swap_pct}% {s.swap_used_gb !== null ? `(${s.swap_used_gb}GB)` : ''}
                      </div>
                    </div>
                  )}
                  {/* Niveau 2 — IOWait */}
                  {s.cpu_iowait_pct !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>I/O WAIT</div>
                      <div style={{ fontSize: 12, color: s.cpu_iowait_pct > 30 ? C.red : s.cpu_iowait_pct > 10 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.cpu_iowait_pct}%
                      </div>
                    </div>
                  )}
                  {/* Niveau 2 — Disk latency */}
                  {s.disk_read_latency_ms !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>DISK LATENCY</div>
                      <div style={{ fontSize: 12, color: s.disk_read_latency_ms > 20 ? C.red : s.disk_read_latency_ms > 10 ? C.orange : C.green, fontWeight: 600 }}>
                        R:{s.disk_read_latency_ms}ms W:{s.disk_write_latency_ms}ms
                      </div>
                    </div>
                  )}
                  {/* Niveau 2 — Net errors */}
                  {(s.net_errors_in !== null && (s.net_errors_in > 0 || s.net_errors_out > 0)) && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>NET ERRORS</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        in:{s.net_errors_in} out:{s.net_errors_out}
                      </div>
                    </div>
                  )}
                  {/* Niveau 3 — Température */}
                  {s.cpu_temp_max_c !== null && s.cpu_temp_max_c > 0 && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>CPU TEMP</div>
                      <div style={{ fontSize: 12, color: s.cpu_temp_max_c > 85 ? C.red : s.cpu_temp_max_c > 75 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.cpu_temp_max_c}°C
                      </div>
                    </div>
                  )}
                  {/* Niveau 3 — SMART */}
                  {s.smart_ok !== null && !s.smart_ok && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.red}40` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>SMART</div>
                      <div style={{ fontSize: 12, color: C.red, fontWeight: 600 }}>
                        FAIL — {s.smart_reallocated_sectors} bad sectors
                      </div>
                    </div>
                  )}
                  {/* Niveau 3 — ZFS */}
                  {s.zfs_available && s.zfs_arc_hit_rate !== null && (
                    <div style={{ padding: '7px 10px', background: C.bg, borderRadius: 6, border: `1px solid ${C.border}` }}>
                      <div style={{ fontSize: 9, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginBottom: 3 }}>ZFS ARC</div>
                      <div style={{ fontSize: 12, color: s.zfs_arc_hit_rate < 70 ? C.red : s.zfs_arc_hit_rate < 85 ? C.orange : C.green, fontWeight: 600 }}>
                        {s.zfs_arc_hit_rate}% hit · {s.zfs_arc_size_gb}GB
                      </div>
                    </div>
                  )}
                  {/* Niveau 3 — Corosync */}
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

              {/* Commande immédiate */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: C.sub, letterSpacing: '0.08em', marginBottom: 7, fontFamily: 'JetBrains Mono, monospace' }}>IMMEDIATE ACTION — {sol.cmdLabel}</div>
                <div style={{ background: '#070d18', borderRadius: 7, border: `1px solid ${C.border}`, overflow: 'hidden' }}>
                  <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', padding: '6px 12px', borderBottom: `1px solid ${C.border}`, background: '#0a1220' }}>
                    <span style={{ fontSize: 11, color: C.muted, fontFamily: 'JetBrains Mono, monospace' }}>bash · {node}</span>
                    <CopyButton text={sol.cmd} />
                  </div>
                  <pre style={{ margin: 0, padding: '11px 15px', fontFamily: 'JetBrains Mono, monospace', fontSize: 12, color: '#7dd3fc', whiteSpace: 'pre-wrap', lineHeight: 1.7 }}>
                    {sol.cmd}
                  </pre>
                </div>
                <div style={{ fontSize: 11, color: C.sub, marginTop: 8, lineHeight: 1.6, paddingLeft: 2 }}>{sol.cmdExpl}</div>
              </div>

              {/* Étapes de résolution */}
              <div style={{ marginBottom: 14 }}>
                <div style={{ fontSize: 10, fontWeight: 700, color: C.sub, letterSpacing: '0.08em', marginBottom: 8, fontFamily: 'JetBrains Mono, monospace' }}>RESOLUTION STEPS</div>
                <div style={{ display: 'flex', flexDirection: 'column', gap: 6 }}>
                  {sol.steps.map((step, i) => (
                    <div key={i} style={{ display: 'flex', gap: 10, alignItems: 'flex-start' }}>
                      <span style={{ fontSize: 10, fontWeight: 700, color: sol.color, fontFamily: 'JetBrains Mono, monospace', width: 18, flexShrink: 0, paddingTop: 1 }}>{i + 1}.</span>
                      <span style={{ fontSize: 13, color: C.sub, lineHeight: 1.5, flex: 1 }}>{step}</span>
                    </div>
                  ))}
                </div>
              </div>

              {/* Long-term */}
              <div style={{ paddingTop: 12, borderTop: `1px solid ${C.border}25`, display: 'flex', justifyContent: 'space-between', alignItems: 'flex-start', gap: 12 }}>
                <div style={{ flex: 1 }}>
                  <span style={{ fontSize: 10, fontWeight: 700, color: C.muted, fontFamily: 'JetBrains Mono, monospace', marginRight: 6 }}>LONG-TERM</span>
                  <span style={{ fontSize: 11, color: C.muted, lineHeight: 1.5 }}>{sol.longTerm}</span>
                </div>
                <a href={`https://${sol.ref}`} target="_blank" rel="noopener noreferrer"
                  style={{ fontSize: 9, color: C.blue, textDecoration: 'none', fontFamily: 'JetBrains Mono, monospace', flexShrink: 0, opacity: 0.7 }}>
                  📖 docs
                </a>
              </div>
            </>
          )}
        </div>
      </div>
    </Card>
  )
}

export function PageRecommendations({ suggestions }) {
  const critCount = suggestions.filter(s => normalizeSeverity(s.severity) === 'CRITICAL').length
  const highCount = suggestions.filter(s => normalizeSeverity(s.severity) === 'HIGH').length

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 16 }}>
      {/* Header */}
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'flex-end' }}>
        <div>
          <h2 style={{ fontSize: 22, fontWeight: 800, color: C.text, marginBottom: 6 }}>Remediation Actions</h2>
          <div style={{ fontSize: 13, color: C.sub }}>
            Actionable remediation playbooks · Thresholds aligned with{' '}
            <a href="https://pve.proxmox.com/pve-docs/pve-admin-guide.html" target="_blank" rel="noopener noreferrer" style={{ color: C.blue, textDecoration: 'none' }}>
              Proxmox VE official standards
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
          <div style={{ fontSize: 15, color: C.sub, marginBottom: 6 }}>No active recommendations</div>
          <div style={{ fontSize: 12, color: C.muted }}>All resources are within Proxmox VE official thresholds</div>
        </Card>
      ) : (
        suggestions.map((s, i) => <RecoCard key={i} s={s} />)
      )}
    </div>
  )
}