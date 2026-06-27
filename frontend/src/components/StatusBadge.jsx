export function StatusBadge({ label, color }) {
  return (
    <span style={{
      padding:'1px 7px', borderRadius:9, fontSize:9, fontWeight:800, letterSpacing:'0.06em',
      background:color+'18', border:`1px solid ${color}45`, color,
      fontFamily:'JetBrains Mono, monospace', whiteSpace:'nowrap'
    }}>
      {label}
    </span>
  )
}