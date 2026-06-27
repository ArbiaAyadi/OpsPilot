import { useState } from 'react'
import { C } from '../utils/colors'

// Rendu Markdown leger : extrait les blocs ```code``` separement du texte,
// rend le texte avec un mini-parseur regex (gras, titres, listes), et les
// blocs de code avec coloration + bouton copier.
export function MD({ text = '', small }) {
  const fs = small ? 12 : 13

  // Nettoyer les artefacts LLM avant tout traitement
  const cleaned = text
    .replace(/```bash\s*\nCopy\s*\n/g, '```bash\n')
    .replace(/```bash\s*\ncopier\s*\n/g, '```bash\n')
    .replace(/bashcopier/g, '')
    .replace(/bash\nCopy\n/g, '')
    .replace(/bash\nCopy/g, '')
    .replace(/bashCopy\n/g, '')
    .replace(/bashCopy/g, '')
    .replace(/bash\ncopier\n/g, '')
    .replace(/bash\ncopier/g, '')

  const segments = []
  const re = /```(\w*)\n?([\s\S]*?)```/g
  let last = 0, m
  while ((m = re.exec(cleaned)) !== null) {
    if (m.index > last) segments.push({ type: 'text', content: cleaned.slice(last, m.index) })
    segments.push({ type: 'code', lang: m[1] || 'bash', content: m[2].trim() })
    last = m.index + m[0].length
  }
  if (last < cleaned.length) segments.push({ type: 'text', content: cleaned.slice(last) })

  const renderText = (t) => {
    t = t.replace(/^\*\*Fix title:\*\*[^\n]*\n?/m, '')
    const html = t
      .replace(/`([^`]+)`/g, `<code style="background:#0f172a;padding:2px 6px;border-radius:4px;font-family:JetBrains Mono,monospace;font-size:11px;color:#a78bfa">$1</code>`)
      .replace(/\*\*([^*]+)\*\*/g, `<strong style="color:#f1f5f9;font-weight:600">$1</strong>`)
      .replace(/^### (.+)$/gm, `<div style="font-size:11px;font-weight:700;color:#64748b;margin:14px 0 6px;text-transform:uppercase;letter-spacing:0.07em">$1</div>`)
      .replace(/^## (.+)$/gm, `<div style="font-size:14px;font-weight:700;color:#94a3b8;margin:14px 0 8px">$1</div>`)
      .replace(/^# (.+)$/gm, `<div style="font-size:16px;font-weight:800;color:#e2e8f0;margin:14px 0 8px">$1</div>`)
      .replace(/^\d+\. (.+)$/gm, `<div style="display:flex;gap:8px;margin:5px 0;color:#94a3b8"><span style="color:#3b82f6;font-family:JetBrains Mono,monospace;font-size:11px;flex-shrink:0">•</span><span>$1</span></div>`)
      .replace(/^- (.+)$/gm, `<div style="display:flex;gap:8px;margin:4px 0;color:#94a3b8"><span style="color:#334155;margin-top:4px;flex-shrink:0">▸</span><span>$1</span></div>`)
      .replace(/\n\n/g, '<br/><br/>').replace(/\n/g, '<br/>')
    return <div dangerouslySetInnerHTML={{ __html: html }} style={{ lineHeight: 1.8, color: '#94a3b8', fontSize: fs }}/>
  }

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 0 }}>
      {segments.map((seg, i) =>
        seg.type === 'code' ? (
          <CodeBlock key={i} lang={seg.lang} content={seg.content}/>
        ) : (
          <div key={i}>{renderText(seg.content)}</div>
        )
      )}
    </div>
  )
}

function CodeBlock({ lang, content }) {
  const [copied, setCopied] = useState(false)
  const copy = () => {
    navigator.clipboard?.writeText(content)
    setCopied(true)
    setTimeout(() => setCopied(false), 2000)
  }
  return (
    <div style={{ margin: '10px 0', borderRadius: 8, overflow: 'hidden', border: `1px solid ${C.border}` }}>
      <div style={{ background: '#060c16', padding: '6px 14px', display: 'flex', justifyContent: 'space-between', alignItems: 'center', borderBottom: `1px solid ${C.border}` }}>
        <span style={{ fontFamily: 'JetBrains Mono, monospace', fontSize: 11, color: C.muted }}>{lang}</span>
        <button
          onClick={copy}
          style={{ background: 'transparent', border: `1px solid ${copied ? C.green : C.border}`, color: copied ? C.green : C.muted, borderRadius: 4, padding: '2px 8px', fontSize: 10, cursor: 'pointer', fontFamily: 'JetBrains Mono, monospace', transition: 'all 0.2s' }}
          onMouseEnter={e => { if (!copied) { e.currentTarget.style.borderColor = C.blue; e.currentTarget.style.color = C.blue } }}
          onMouseLeave={e => { if (!copied) { e.currentTarget.style.borderColor = C.border; e.currentTarget.style.color = C.muted } }}
        >
          {copied ? '✓ copied' : 'copy'}
        </button>
      </div>
      <pre style={{ background: '#030812', margin: 0, padding: '14px 16px', overflowX: 'auto', fontSize: 12, lineHeight: 1.7, fontFamily: 'JetBrains Mono, monospace', color: '#7dd3fc', whiteSpace: 'pre' }}>
        {content}
      </pre>
    </div>
  )
}
