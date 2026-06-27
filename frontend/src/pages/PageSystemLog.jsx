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
          <h2 style={{ fontSize:18, fontWeight:700, color:C.text, marginBottom:4 }}>System Audit Log</h2>
          <div style={{ fontSize:12, color:C.sub }}>Real-time operational event stream</div>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:8 }}>
          <span style={{ width:8, height:8, borderRadius:'50%', background:C.green, display:'inline-block', animation:'pulse 1.5s infinite' }}/>
          <span style={{ fontSize:11, color:C.green, fontFamily:'JetBrains Mono, monospace' }}>LIVE</span>
        </div>
      </div>
      <Card style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', padding:0 }}>
        <div style={{ padding:'10px 16px', borderBottom:`1px solid ${C.border}`, background:C.bg, display:'grid', gridTemplateColumns:'70px 110px 1fr', gap:8, fontSize:10, fontFamily:'JetBrains Mono, monospace', color:C.muted, letterSpacing:'0.08em' }}>
          <span>TIME</span><span>COMPONENT</span><span>EVENT</span>
        </div>
        <div ref={ref} style={{ flex:1, overflowY:'auto', maxHeight:'calc(100vh - 300px)' }}>
          {agentLog.length === 0 ? (
            <div style={{ color:C.muted, fontSize:12, textAlign:'center', padding:'40px 0', fontStyle:'italic' }}>
              Waiting for activity...
            </div>
          ) : (
            [...agentLog].reverse().map((l, i) => (
              <div key={i} style={{ display:'grid', gridTemplateColumns:'70px 110px 1fr', gap:8, padding:'6px 16px', fontSize:12, fontFamily:'JetBrains Mono, monospace', background: i%2===0 ? 'transparent' : C.bg+'60', borderBottom:`1px solid ${C.border}10` }}>
                <span style={{ color:C.muted }}>{l.time}</span>
                <span style={{ color:l.color||C.sub, overflow:'hidden', textOverflow:'ellipsis', whiteSpace:'nowrap', fontWeight:600 }}>{l.label}</span>
                <span style={{ color:C.sub }}>{l.msg}</span>
              </div>
            ))
          )}
        </div>
        {agentLog.length > 0 && (
          <div style={{ padding:'8px 16px', borderTop:`1px solid ${C.border}`, fontSize:10, color:C.muted, fontFamily:'JetBrains Mono, monospace', display:'flex', justifyContent:'space-between' }}>
            <span>{agentLog.length} event{agentLog.length > 1 ? 's' : ''} recorded this session</span>
            <span style={{ color:C.border }}>In-memory · resets on restart</span>
          </div>
        )}
      </Card>
    </div>
  )
}