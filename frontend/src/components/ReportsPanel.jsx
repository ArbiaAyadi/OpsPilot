import { C } from '../utils/colors'

export function ReportsPanel({ rapports, onOpen }) {
  if(!rapports.length) return <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'24px 0' }}>No reports yet</div>
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:6 }}>
      {rapports.map((r,i)=>{
        const isCrit=r.nom.includes('critique')||r.nom.includes('critique'), isImp=r.nom.includes('important')
        const ac=isCrit?C.red:isImp?C.orange:C.green
        return (
          <div key={i} onClick={()=>onOpen(r)} style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:8, padding:'10px 12px', cursor:'pointer', transition:'border-color 0.15s' }}
            onMouseEnter={e=>e.currentTarget.style.borderColor=ac}
            onMouseLeave={e=>e.currentTarget.style.borderColor=C.border}
          >
            <div style={{ fontSize:11, color:C.sub, fontFamily:'JetBrains Mono, monospace', marginBottom:4, wordBreak:'break-all' }}>{r.nom}</div>
            <div style={{ display:'flex', justifyContent:'space-between', fontSize:10, color:C.muted, fontFamily:'JetBrains Mono, monospace' }}>
              <span style={{ color:ac }}>{isCrit?'● CRITICAL':isImp?'● IMPORTANT':'● INFO'}</span>
              <span>{r.date?.slice(11,19)}</span>
            </div>
          </div>
        )
      })}
    </div>
  )
}