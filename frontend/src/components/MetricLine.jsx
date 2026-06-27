import { C } from '../utils/colors'
import { statusOf } from '../styles/theme'
import { StatusBadge } from './StatusBadge'

// Ligne metrique compacte : label, valeur formattee, barre optionnelle, badge de statut
export function MetricLine({ label, display, value, max = 100, warn, crit, inverse = false, unit = '', showBar = true }) {
  const st = (warn != null && crit != null) ? statusOf(value, warn, crit, inverse) : null
  const barColor = st ? st.color : C.blue
  const pct = max ? Math.min(100, Math.max(0, (value / max) * 100)) : 0
  return (
    <div style={{ marginBottom: 9 }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', fontSize:11, marginBottom: showBar ? 4 : 0 }}>
        <span style={{ color: C.sub }}>{label}</span>
        <div style={{ display:'flex', gap:7, alignItems:'center' }}>
          <span style={{ color: st ? st.color : C.text, fontWeight:700, fontFamily:'JetBrains Mono, monospace', fontSize:11.5 }}>{display}{unit}</span>
          {st && <StatusBadge label={st.label} color={st.color}/>}
        </div>
      </div>
      {showBar && (
        <div style={{ height:3, background:C.border, borderRadius:2 }}>
          <div style={{ height:'100%', width:`${pct}%`, background:barColor, borderRadius:2, transition:'width 0.7s ease' }}/>
        </div>
      )}
    </div>
  )
}