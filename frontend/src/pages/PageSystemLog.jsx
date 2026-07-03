import { useRef, useEffect } from 'react'
import { C } from '../utils/colors'
import { Card } from '../components/Card'

export function PageSystemLog({ agentLog }) {
  const ref = useRef(null)
  useEffect(() => { ref.current?.scrollTo(0, ref.current.scrollHeight) }, [agentLog])
  return (
    <div style={{ display:'flex', flexDirection:'column', gap:16, height:'100%' }}>
      <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center' }}>
        <div>
          <h2 style={{ fontSize:22, fontWeight:800, color:C.text, marginBottom:6 }}>System Audit Log</h2>
          <div style={{ fontSize:13, color:C.sub }}>Real-time operational event stream</div>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <span style={{ width:8, height:8, borderRadius:'50%', background:C.green, display:'inline-block', animation:'pulse 1.5s infinite' }}/>
          <span style={{ fontSize:12, color:C.green, fontFamily:'JetBrains Mono, monospace', fontWeight:700 }}>LIVE</span>
        </div>
      </div>
      <Card style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', padding:0 }}>
        <div style={{ padding:'11px 18px', borderBottom:`1px solid ${C.border}`, background:C.bg, display:'grid', gridTemplateColumns:'80px 120px 1fr', gap:12, fontSize:11, fontFamily:'JetBrains Mono, monospace', color:C.muted, letterSpacing:'0.1em', fontWeight:700 }}>
          <span>TIME</span><span>COMPONENT</span><span>EVENT</span>
        </div>
        <div ref={ref} style={{ flex:1, overflowY:'auto', maxHeight:'calc(100vh - 300px)' }}>
          {agentLog.length === 0 ? (
            <div style={{ color:C.muted, fontSize:13, textAlign:'center', padding:'48px 0', fontStyle:'italic' }}>
              Waiting for activity...
            </div>
          ) : (
            [...agentLog].reverse().map((l, i) => (
              <div key={i} style={{ display:'grid', gridTemplateColumns:'80px 120px 1fr', gap:12, padding:'8px 18px', fontSize:13, fontFamily:'JetBrains Mono, monospace', background: i%2===0 ? 'transparent' : C.bg+'60', borderBottom:`1px solid ${C.border}18`, transition:'background 0.1s' }}>
                <span style={{ color:C.muted }}>{l.time}</span>
                <span style={{ color:l.color||C.sub, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontWeight:700 }}>{l.label}</span>
                <span style={{ color:C.sub }}>{l.msg}</span>
              </div>
            ))
          )}
        </div>
        {agentLog.length > 0 && (
          <div style={{ padding:'10px 18px', borderTop:`1px solid ${C.border}`, fontSize:11, color:C.muted, fontFamily:'JetBrains Mono, monospace', display:'flex', justifyContent:'space-between' }}>
            <span style={{ fontWeight:600 }}>{agentLog.length} event{agentLog.length > 1 ? 's' : ''} recorded this session</span>
            <span style={{ color:C.border }}>In-memory · resets on restart</span>
          </div>
        )}
      </Card>
    </div>
  )
}