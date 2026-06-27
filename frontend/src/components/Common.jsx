import { C } from '../utils/colors'
import { riskColor } from '../styles/theme'

export function SectionLabel({ children, color = C.sub }) {
  return (
    <div style={{
      fontSize: 10, fontWeight: 700, letterSpacing: '0.14em', color,
      textTransform: 'uppercase', fontFamily: 'JetBrains Mono, monospace', marginBottom: 14
    }}>
      {children}
    </div>
  )
}

export function Chip({ label, color }) {
  const c = color || C.sub
  return (
    <span style={{
      padding: '2px 10px', borderRadius: 20, fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
      fontFamily: 'JetBrains Mono, monospace', background: c+'18', border: `1px solid ${c}45`,
      color: c, whiteSpace: 'nowrap'
    }}>
      {label}
    </span>
  )
}

export function MiniGroupLabel({ children }) {
  return (
    <div style={{
      fontSize:9, fontWeight:700, color:C.muted, letterSpacing:'0.1em', textTransform:'uppercase',
      fontFamily:'JetBrains Mono, monospace', marginBottom:8, marginTop:14,
      display:'flex', alignItems:'center', gap:8
    }}>
      <span>{children}</span><div style={{ flex:1, height:1, background:C.border }}/>
    </div>
  )
}

export function MetricRow({ label, value = 0, used, total }) {
  const c = riskColor(value)
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 4 }}>
        <span style={{ color: C.sub }}>{label}</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {used != null && total != null && <span style={{ color: C.muted, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>{used} / {total} GB</span>}
          <span style={{ color: c, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace' }}>{value?.toFixed(1)}%</span>
        </div>
      </div>
      <div style={{ height: 4, background: C.border, borderRadius: 2 }}>
        <div style={{ height: '100%', width: `${Math.min(100, value||0)}%`, background: c, borderRadius: 2, transition: 'width 0.7s ease' }}/>
      </div>
    </div>
  )
}

export function Placeholder({ icon='◈', text }) {
  return (
    <div style={{ display:'flex', alignItems:'center', justifyContent:'center', height:300 }}>
      <div style={{ textAlign:'center', color:C.muted }}>
        <div style={{ fontSize:36, marginBottom:12 }}>{icon}</div>
        <div style={{ fontSize:14 }}>{text}</div>
      </div>
    </div>
  )
}