import { useState, useRef, useEffect, useCallback } from 'react'
import { C } from '../utils/colors'
import { MD } from '../components/MD'

const QUICK = [
  { label: 'Create nginx web VM',          q: 'I want to create an nginx VM on pve1. What resources do you recommend?' },
  { label: 'PostgreSQL database VM',       q: 'I want a PostgreSQL VM on pve2 for ~100 connections. Size it appropriately.' },
  { label: 'Docker VM setup',              q: 'I want to deploy Docker on a Proxmox VM. What is the optimal configuration?' },
  { label: 'Live migrate VM',              q: 'How do I migrate linux-vm1 from pve1 to pve2 without downtime?' },
  { label: 'Optimize cluster memory',      q: 'How can I optimize memory usage across VMs in this cluster?' },
  { label: 'Proxmox backup strategy',      q: 'What is the best backup strategy for this cluster?' },
  { label: 'Kubernetes node VM',           q: 'I want to create a Kubernetes node on Proxmox. What resources are recommended?' },
  { label: 'Diagnose high CPU',            q: 'A VM has permanently high CPU. How do I diagnose and fix it?' },
]

// ── Panneau conversations (sidebar gauche) ────────────────────────────────────
function ConversationPanel({ conversations, activeId, onNew, onSwitch, onDelete }) {
  return (
    <div style={{
      width: 220, flexShrink: 0,
      background: '#060d1a',
      borderRight: `1px solid ${C.border}`,
      display: 'flex', flexDirection: 'column',
      overflow: 'hidden',
    }}>
      {/* Header */}
      <div style={{ padding: '12px 12px 8px', borderBottom: `1px solid ${C.border}` }}>
        <button
          onClick={onNew}
          style={{
            width: '100%', padding: '8px 12px',
            background: C.blue, border: 'none', borderRadius: 8,
            color: '#fff', fontSize: 12, fontWeight: 600,
            cursor: 'pointer', display: 'flex', alignItems: 'center',
            gap: 8, justifyContent: 'center', transition: 'opacity 0.15s',
          }}
          onMouseEnter={e => e.currentTarget.style.opacity = '0.85'}
          onMouseLeave={e => e.currentTarget.style.opacity = '1'}
        >
          + New conversation
        </button>
      </div>

      {/* Liste des conversations */}
      <div style={{ flex: 1, overflowY: 'auto', padding: '6px 8px' }}>
        {conversations.length === 0 ? (
          <div style={{ fontSize: 11, color: C.muted, textAlign: 'center', padding: '20px 0' }}>
            No conversations yet
          </div>
        ) : (
          conversations.map(conv => (
            <div
              key={conv.id}
              onClick={() => onSwitch(conv.id)}
              style={{
                padding: '8px 10px', borderRadius: 7, marginBottom: 3,
                background: conv.active ? 'rgba(59,130,246,0.12)' : 'transparent',
                border: `1px solid ${conv.active ? 'rgba(59,130,246,0.3)' : 'transparent'}`,
                cursor: 'pointer', transition: 'all 0.12s',
                display: 'flex', alignItems: 'flex-start', gap: 8,
                position: 'relative',
              }}
              onMouseEnter={e => {
                if (!conv.active) e.currentTarget.style.background = 'rgba(255,255,255,0.04)'
                e.currentTarget.querySelector('.del-btn').style.opacity = '1'
              }}
              onMouseLeave={e => {
                if (!conv.active) e.currentTarget.style.background = 'transparent'
                e.currentTarget.querySelector('.del-btn').style.opacity = '0'
              }}
            >
              <span style={{ fontSize: 14, flexShrink: 0, marginTop: 1 }}>💬</span>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{
                  fontSize: 12, color: conv.active ? C.text : C.sub,
                  fontWeight: conv.active ? 600 : 400,
                  overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap',
                  lineHeight: 1.4,
                }}>
                  {conv.preview || 'New conversation'}
                </div>
                <div style={{ fontSize: 10, color: C.muted, marginTop: 2, fontFamily: 'JetBrains Mono, monospace' }}>
                  {conv.count} msg{conv.count !== 1 ? 's' : ''}
                </div>
              </div>
              <button
                className="del-btn"
                onClick={e => { e.stopPropagation(); onDelete(conv.id) }}
                style={{
                  opacity: 0, background: 'transparent', border: 'none',
                  color: C.muted, cursor: 'pointer', fontSize: 12, padding: '2px 4px',
                  borderRadius: 4, transition: 'all 0.15s', flexShrink: 0,
                }}
                onMouseEnter={e => { e.currentTarget.style.color = C.red }}
                onMouseLeave={e => { e.currentTarget.style.color = C.muted }}
                title="Delete conversation"
              >✕</button>
            </div>
          ))
        )}
      </div>
    </div>
  )
}

// ── Message utilisateur avec édition ─────────────────────────────────────────
function UserMessage({ msg, onEdit, thinking }) {
  const [hovered, setHovered] = useState(false)
  const [editing, setEditing] = useState(false)
  const [editVal, setEditVal] = useState(msg.content)
  const taRef = useRef(null)
  useEffect(() => {
    if (editing && taRef.current) {
      taRef.current.focus()
      taRef.current.style.height = 'auto'
      taRef.current.style.height = taRef.current.scrollHeight + 'px'
    }
  }, [editing])
  const submit = () => {
    const v = editVal.trim()
    if (!v || v === msg.content) { setEditing(false); return }
    onEdit(msg.id, v); setEditing(false)
  }
  return (
    <div
      style={{ display:'flex', gap:12, padding:'8px 0', flexDirection:'row-reverse', alignItems:'flex-start', animation:'fadeUp 0.3s ease' }}
      onMouseEnter={() => setHovered(true)}
      onMouseLeave={() => setHovered(false)}
    >
      {hovered && !editing && !thinking && (
        <button onClick={() => { setEditVal(msg.content); setEditing(true) }}
          title="Edit message"
          style={{ width:28, height:28, borderRadius:7, border:`1px solid ${C.borderHi}`, background:C.surface, color:C.sub, cursor:'pointer', display:'flex', alignItems:'center', justifyContent:'center', fontSize:13, flexShrink:0, marginTop:4, transition:'all 0.15s' }}
          onMouseEnter={e => { e.currentTarget.style.borderColor = C.blue; e.currentTarget.style.color = C.blue }}
          onMouseLeave={e => { e.currentTarget.style.borderColor = C.borderHi; e.currentTarget.style.color = C.sub }}
        >✏</button>
      )}
      <div style={{ maxWidth:'78%', background:'#0a1828', border:`1px solid ${editing ? C.blue+'60' : C.blue+'30'}`, borderRadius:'14px 3px 14px 14px', padding: editing ? '10px 12px' : '12px 16px', transition:'border-color 0.2s' }}>
        {editing ? (
          <>
            <textarea ref={taRef} value={editVal}
              onChange={e => { setEditVal(e.target.value); e.target.style.height = 'auto'; e.target.style.height = e.target.scrollHeight + 'px' }}
              onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); submit() } if (e.key === 'Escape') { setEditing(false); setEditVal(msg.content) } }}
              style={{ width:'100%', background:'transparent', border:'none', outline:'none', color:C.text, fontSize:13, lineHeight:1.6, fontFamily:"'Inter',sans-serif", resize:'none', minHeight:40 }}
            />
            <div style={{ display:'flex', gap:8, marginTop:10, justifyContent:'flex-end' }}>
              <button onClick={() => { setEditing(false); setEditVal(msg.content) }}
                style={{ padding:'4px 12px', borderRadius:6, border:`1px solid ${C.border}`, background:'transparent', color:C.sub, cursor:'pointer', fontSize:12 }}>
                Cancel
              </button>
              <button onClick={submit}
                style={{ padding:'4px 14px', borderRadius:6, border:'none', background:C.blue, color:'#fff', cursor:'pointer', fontSize:12, fontWeight:600 }}>
                Send ↑
              </button>
            </div>
          </>
        ) : (
          <>
            <div style={{ fontSize:13, color:C.text, lineHeight:1.75 }}>{msg.content}</div>
            <div style={{ fontSize:11, color:C.muted, marginTop:8, fontFamily:'JetBrains Mono, monospace', display:'flex', gap:8, alignItems:'center' }}>
              {msg.timestamp?.slice(11,19)}
              {msg.edited && <span style={{ color:C.muted, fontSize:9 }}>✏ edited</span>}
            </div>
          </>
        )}
      </div>
    </div>
  )
}

// ── Page principale ───────────────────────────────────────────────────────────
// ← MODIFIÉ : ce chat reste désormais strictement question/réponse.
// Les alertes automatiques d'incident ne sont plus jamais poussées dans
// "messages" (voir App.jsx, handler WebSocket type==='alerte' -- le
// setChatMessages(...) correspondant a été retiré) ; elles continuent
// d'alimenter Recommendations, Incidents et System Log normalement,
// simplement plus mélangées à cette conversation. Le rendu "AUTOMATED
// INCIDENT ALERT" (msg.type==='alerte') est retiré ici en conséquence --
// mort depuis ce changement, jamais plus atteint en pratique.
export function PageAssistant({ messages, thinking, input, setInput, onSend, onEdit, onClear, connected, send }) {
  const endRef = useRef(null)
  const [conversations, setConversations] = useState([])

  useEffect(() => { endRef.current?.scrollIntoView({ behavior:'smooth' }) }, [messages, thinking])

  // Rafraîchir la liste des conversations quand une réponse arrive
  useEffect(() => {
    if (!thinking) {
      fetch('/api/conversations')
        .then(r => r.json())
        .then(d => setConversations(d.conversations || []))
        .catch(() => {})
    }
  }, [thinking])

  // Rafraîchir aussi au montage
  useEffect(() => {
    fetch('/api/conversations')
      .then(r => r.json())
      .then(d => setConversations(d.conversations || []))
      .catch(() => {})
  }, [])

  const refreshConversations = useCallback(() => {
    fetch('/api/conversations')
      .then(r => r.json())
      .then(d => setConversations(d.conversations || []))
      .catch(() => {})
  }, [])

  const handleNew = useCallback(() => {
    if (send) {
      send({ type: 'new_conversation' })
      setTimeout(refreshConversations, 300)
    }
  }, [send, refreshConversations])

  const handleSwitch = useCallback((convId) => {
    if (send) {
      send({ type: 'switch_conversation', conv_id: convId })
      setTimeout(refreshConversations, 300)
    }
  }, [send, refreshConversations])

  const handleDelete = useCallback((convId) => {
    if (send) {
      send({ type: 'delete_conversation', conv_id: convId })
      setTimeout(refreshConversations, 300)
    }
  }, [send, refreshConversations])

  return (
    <div style={{ display:'flex', height:'100%', overflow:'hidden' }}>

      {/* ── Sidebar conversations ── */}
      <ConversationPanel
        conversations={conversations}
        onNew={handleNew}
        onSwitch={handleSwitch}
        onDelete={handleDelete}
      />

      {/* ── Zone de chat principale ── */}
      <div style={{ flex:1, display:'flex', flexDirection:'column', overflow:'hidden', minWidth:0 }}>
        <div style={{ flex:1, overflowY:'auto', paddingBottom:16 }}>

          {/* Welcome screen */}
          {messages.length === 0 && (
            <div style={{ maxWidth:620, margin:'40px auto', animation:'fadeUp 0.4s ease', padding:'0 16px' }}>
              <div style={{ textAlign:'center', marginBottom:28 }}>
                {/* ← RETIRÉ : pastille hexagonale bleue au-dessus du
                    titre -- le titre en dégradé suffit à porter l'accent. */}
                <div style={{ fontSize:22, fontWeight:800, color:C.text, letterSpacing:'-0.02em', marginBottom:6 }}>OpsPilot AI Assistant</div>
                <div style={{ fontSize:13, color:C.muted }}>Infrastructure expert · Proxmox VE · Based on live cluster data</div>
              </div>
              <div style={{ display:'grid', gridTemplateColumns:'1fr 1fr', gap:8 }}>
                {QUICK.map((a, i) => (
                  <button key={i} onClick={() => { setInput(a.q); setTimeout(() => onSend(a.q), 50) }}
                    style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:10, padding:'12px 16px', cursor:'pointer', textAlign:'left', fontSize:13, color:C.sub, lineHeight:1.5, transition:'all 0.15s', display:'flex', alignItems:'center', gap:10 }}
                    onMouseEnter={e => { e.currentTarget.style.borderColor = C.blue; e.currentTarget.style.color = C.text }}
                    onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.sub }}
                  >
                    <span style={{ color:C.muted, fontSize:14 }}>→</span>{a.label}
                  </button>
                ))}
              </div>
            </div>
          )}

          <div style={{ maxWidth:760, margin:'0 auto', padding:'0 16px' }}>
            {messages.map((msg, i) => {
              const isUser  = msg.role === 'user'
              const isSys   = msg.type === 'systeme'

              if (isSys) return (
                <div key={msg.id||i} style={{ textAlign:'center', padding:'10px 0', animation:'fadeUp 0.3s ease' }}>
                  <div style={{ display:'inline-block', background:C.card, border:`1px solid ${C.border}`, borderRadius:20, padding:'6px 18px', fontSize:13, color:C.sub }}>
                    <MD text={msg.content} small/>
                  </div>
                </div>
              )
              if (isUser) return <UserMessage key={msg.id||i} msg={msg} onEdit={onEdit} thinking={thinking}/>
              return (
                <div key={msg.id||i} style={{ display:'flex', gap:12, padding:'8px 0', alignItems:'flex-start', animation:'fadeUp 0.3s ease' }}>
                  <div style={{ width:34, height:34, borderRadius:9, flexShrink:0, marginTop:2, background:'#080e22', border:`1px solid ${C.border}`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:15 }}>
                    ⬡
                  </div>
                  <div style={{ maxWidth:'78%', background:C.card, border:`1px solid ${C.border}`, borderRadius:'3px 14px 14px 14px', padding:'12px 16px' }}>
                    <MD text={msg.content}/>
                    <div style={{ fontSize:11, color:C.muted, marginTop:8, textAlign:'right', fontFamily:'JetBrains Mono, monospace', display:'flex', justifyContent:'flex-end', gap:10, alignItems:'center' }}>
                      {msg.llm && <span style={{ color:msg.llm==='claude'?C.purple:C.cyan, fontSize:9, fontWeight:700, letterSpacing:'0.08em' }}>◆ {(msg.model||msg.llm||'groq').toUpperCase()}</span>}
                      {msg.timestamp?.slice(11,19)}
                    </div>
                  </div>
                </div>
              )
            })}

            {/* Thinking indicator */}
            {thinking && (
              <div style={{ display:'flex', gap:12, padding:'8px 0', animation:'fadeUp 0.2s ease' }}>
                <div style={{ width:34, height:34, borderRadius:9, background:'#080e22', border:`1px solid ${C.border}`, display:'flex', alignItems:'center', justifyContent:'center', fontSize:15, flexShrink:0 }}>⬡</div>
                <div style={{ background:C.card, border:`1px solid ${C.border}`, borderRadius:'3px 14px 14px 14px', padding:'14px 18px', display:'flex', gap:5, alignItems:'center' }}>
                  {[0,1,2].map(j => (
                    <span key={j} style={{ width:6, height:6, borderRadius:'50%', background:C.muted, display:'inline-block', animation:`pulse 1.2s infinite ${j*0.2}s` }}/>
                  ))}
                  <span style={{ fontSize:11, color:C.muted, marginLeft:8, fontFamily:'JetBrains Mono, monospace' }}>Analyzing cluster data...</span>
                </div>
              </div>
            )}
            <div ref={endRef}/>
          </div>
        </div>

        {/* Input zone */}
        <div style={{ borderTop:`1px solid ${C.border}`, paddingTop:16, flexShrink:0, padding:'12px 16px' }}>
          <div style={{ maxWidth:760, margin:'0 auto' }}>
            <div
              style={{ display:'flex', gap:10, background:C.card, border:`1px solid ${C.border}`, borderRadius:12, padding:'10px 14px' }}
              onFocusCapture={e => e.currentTarget.style.borderColor = C.blue}
              onBlurCapture={e  => e.currentTarget.style.borderColor = C.border}
            >
              <textarea value={input} onChange={e => setInput(e.target.value)}
                onKeyDown={e => { if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); onSend() } }}
                placeholder="Ask about your Proxmox infrastructure... (Enter to send)"
                rows={1}
                style={{ flex:1, background:'transparent', border:'none', outline:'none', color:C.text, fontSize:13, lineHeight:1.6, fontFamily:"'Inter',sans-serif", resize:'none', maxHeight:120, overflowY:'auto' }}
                onInput={e => { e.target.style.height = 'auto'; e.target.style.height = Math.min(e.target.scrollHeight, 120) + 'px' }}
              />
              <button onClick={() => onSend()} disabled={!input.trim() || thinking || !connected}
                style={{ width:36, height:36, borderRadius:8, border:'none', flexShrink:0, background: input.trim()&&!thinking&&connected ? C.blue : C.surface, color:'#fff', cursor: input.trim()&&!thinking&&connected ? 'pointer' : 'default', display:'flex', alignItems:'center', justifyContent:'center', fontSize:16, transition:'background 0.2s' }}>
                {thinking
                  ? <span style={{ width:14, height:14, border:`2px solid ${C.muted}`, borderTopColor:C.text, borderRadius:'50%', display:'inline-block', animation:'spin 0.8s linear infinite' }}/>
                  : '↑'}
              </button>
            </div>
            <div style={{ display:'flex', justifyContent:'space-between', alignItems:'center', marginTop:8 }}>
              <div style={{ fontSize:11, color:C.muted }}>✏ Hover messages to edit · Proxmox VE infrastructure only</div>
              {messages.filter(m => m.role === 'user' || m.role === 'assistant').length > 0 && (
                <button onClick={onClear}
                  style={{ fontSize:11, color:C.muted, background:'transparent', border:`1px solid ${C.border}`, borderRadius:6, padding:'3px 10px', cursor:'pointer', transition:'all 0.15s' }}
                  onMouseEnter={e => { e.currentTarget.style.borderColor = C.red; e.currentTarget.style.color = C.red }}
                  onMouseLeave={e => { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.muted }}
                >🗑 Clear history</button>
              )}
            </div>
          </div>
        </div>
      </div>
    </div>
  )
}