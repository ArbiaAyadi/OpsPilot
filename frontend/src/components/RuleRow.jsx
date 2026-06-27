import { C } from '../utils/colors'

// RuleRow depliable — l'etat ouvert/ferme est gere par le PARENT (PageMonitoringRules)
// via isOpen/onToggle pour survivre aux re-renders du parent (cycles WebSocket
// toutes les 60s qui reinitialiseraient un useState local a false).
export function RuleRow({ r, color, isAI, isOpen, onToggle }) {
  return (
    <div style={{ borderRadius:8, overflow:'hidden', border:`1px solid ${isOpen ? color+'40' : C.border}`, transition:'border-color 0.2s' }}>
      <div
        onClick={onToggle}
        style={{ display:'flex', justifyContent:'space-between', alignItems:'center', padding:'11px 16px', cursor:'pointer', userSelect:'none', background:isOpen?C.surface:C.card }}
      >
        <div style={{ display:'flex', alignItems:'center', gap:10, flex:1, minWidth:0 }}>
          <div style={{ width:3, height:32, background:color, borderRadius:2, flexShrink:0 }}/>
          <div style={{ minWidth:0 }}>
            <div style={{ display:'flex', alignItems:'center', gap:8, flexWrap:'wrap' }}>
              <code style={{ fontSize:11, fontFamily:'JetBrains Mono,monospace', color:'#7dd3fc', background:C.bg, padding:'2px 7px', borderRadius:4, whiteSpace:'nowrap' }}>{r.metric}</code>
              <span style={{ fontSize:12, color:C.text, fontFamily:'JetBrains Mono,monospace', fontWeight:600 }}>{r.op} {r.seuil}</span>
              {r.dur !== '—' && <span style={{ fontSize:10, color:C.muted, fontFamily:'JetBrains Mono,monospace' }}>for {r.dur}</span>}
            </div>
            <div style={{ fontSize:10, color:C.muted, marginTop:3, fontFamily:'JetBrains Mono,monospace' }}>SOURCE: {r.src}</div>
          </div>
        </div>
        <div style={{ display:'flex', alignItems:'center', gap:6, flexShrink:0, marginLeft:8 }}>
          <span style={{ padding:'2px 8px', borderRadius:10, fontSize:9, fontWeight:700, background:color+'18', border:`1px solid ${color}40`, color, whiteSpace:'nowrap' }}>{r.sev}</span>
          <span style={{ padding:'2px 8px', borderRadius:10, fontSize:9, fontWeight:700, background:isAI?'#a855f718':'#3b82f618', border:`1px solid ${isAI?'#a855f740':'#3b82f640'}`, color:isAI?'#a855f7':'#3b82f6' }}>{isAI?'AI':'DOC'}</span>
          <span style={{ color:C.muted, fontSize:10, display:'inline-block', transition:'transform 0.25s', transform: isOpen?'rotate(180deg)':'none', flexShrink:0 }}>▼</span>
        </div>
      </div>
      {isOpen && (
        <div style={{ background:C.surface, borderTop:`1px solid ${C.border}` }}>
          <div style={{ padding:'14px 16px 16px' }}>
            <p style={{ fontSize:13, color:C.sub, lineHeight:1.8, margin:'0 0 12px 0' }}>{r.desc}</p>
            {r.cmd && (
              <div>
                <div style={{ fontSize:9, color:C.muted, fontFamily:'JetBrains Mono,monospace', marginBottom:6, letterSpacing:'0.08em' }}>DIAGNOSTIC COMMANDS</div>
                <pre style={{ background:'#020810', borderRadius:7, padding:'12px 16px', fontSize:12, fontFamily:'JetBrains Mono,monospace', color:'#7dd3fc', margin:0, overflowX:'auto', whiteSpace:'pre', lineHeight:1.8, border:`1px solid #1e3a5f` }}>{r.cmd}</pre>
              </div>
            )}
          </div>
        </div>
      )}
    </div>
  )
}