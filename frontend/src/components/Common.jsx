import { C } from '../utils/colors'
import { riskColor } from '../styles/theme'

/**
 * ← MODIFIÉ (refonte visuelle) : les 5 composants existants
 * (SectionLabel, Chip, MiniGroupLabel, MetricRow, Placeholder) gardent
 * EXACTEMENT leur signature -- aucun appelant à modifier. Seul leur
 * rendu évolue. PageHero est AJOUTÉ en fin de fichier.
 */

export function SectionLabel({ children, color = C.sub }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 10,
      fontSize: 10, fontWeight: 700, letterSpacing: '0.14em', color,
      textTransform: 'uppercase', fontFamily: 'JetBrains Mono, monospace',
      marginBottom: 14,
    }}>
      {/* ← Petit trait d'accent avant le libellé : repère visuel qui
          structure la page sans ajouter de texte. */}
      <span style={{ width: 3, height: 12, borderRadius: 2, background: color === C.sub ? C.accent : color, flexShrink: 0 }}/>
      <span>{children}</span>
    </div>
  )
}

export function Chip({ label, color }) {
  const c = color || C.sub
  return (
    <span style={{
      padding: '3px 11px', borderRadius: 20, fontSize: 10, fontWeight: 700, letterSpacing: '0.08em',
      fontFamily: 'JetBrains Mono, monospace',
      background: c + '16', border: `1px solid ${c}40`,
      color: c, whiteSpace: 'nowrap',
    }}>
      {label}
    </span>
  )
}

export function MiniGroupLabel({ children }) {
  return (
    <div style={{
      fontSize: 9, fontWeight: 700, color: C.muted, letterSpacing: '0.1em', textTransform: 'uppercase',
      fontFamily: 'JetBrains Mono, monospace', marginBottom: 8, marginTop: 14,
      display: 'flex', alignItems: 'center', gap: 8,
    }}>
      <span>{children}</span>
      <div style={{ flex: 1, height: 1, background: `linear-gradient(90deg, ${C.border}, transparent)` }}/>
    </div>
  )
}

export function MetricRow({ label, value = 0, used, total }) {
  const c = riskColor(value)
  return (
    <div style={{ marginBottom: 10 }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 12, marginBottom: 5 }}>
        <span style={{ color: C.sub }}>{label}</span>
        <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
          {used != null && total != null && (
            <span style={{ color: C.muted, fontSize: 11, fontFamily: 'JetBrains Mono, monospace' }}>{used} / {total} GB</span>
          )}
          <span style={{ color: c, fontWeight: 700, fontFamily: 'JetBrains Mono, monospace' }}>{value?.toFixed(1)}%</span>
        </div>
      </div>
      <div style={{ height: 5, background: C.bg, borderRadius: 3, overflow: 'hidden' }}>
        <div style={{
          height: '100%', width: `${Math.min(100, value || 0)}%`,
          background: `linear-gradient(90deg, ${c}bb, ${c})`,
          borderRadius: 3, transition: 'width 0.7s ease',
        }}/>
      </div>
    </div>
  )
}

export function Placeholder({ icon = '◈', text }) {
  return (
    <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: 300 }}>
      <div style={{ textAlign: 'center', color: C.muted }}>
        <div style={{
          width: 56, height: 56, borderRadius: 16, margin: '0 auto 16px',
          background: C.accentSoft, border: `1px solid ${C.accentLine}`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          fontSize: 24, color: C.accent,
        }}>{icon}</div>
        <div style={{ fontSize: 14 }}>{text}</div>
      </div>
    </div>
  )
}

// ══════════════════════════════════════════════════════════════════════════
// ← AJOUT : PageHero
// ══════════════════════════════════════════════════════════════════════════
// En-tête de section reprenant le motif de la référence visuelle : une
// icône dans un carré arrondi teinté, un titre, une description, et un
// emplacement libre à droite pour des actions ou des indicateurs. Donne à
// chaque page un point d'entrée clair au lieu d'attaquer directement sur
// une grille de données.
//
// Purement additif : aucune page existante ne l'utilise, rien ne casse.
// À poser en haut d'une page pour lui donner la même allure que la
// référence.
export function PageHero({ icon, title, description, right, accent = C.accent }) {
  return (
    <div style={{
      display: 'flex', alignItems: 'center', gap: 18,
      background: `linear-gradient(120deg, ${accent}0d 0%, ${C.card} 45%)`,
      border: `1px solid ${C.border}`,
      borderRadius: C.radius,
      padding: '20px 24px',
      boxShadow: C.glowSoft,
    }}>
      {icon != null && (
        <div style={{
          width: 46, height: 46, borderRadius: 12, flexShrink: 0,
          background: accent + '18', border: `1px solid ${accent}3a`,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          color: accent, fontSize: 20,
        }}>
          {icon}
        </div>
      )}
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 17, fontWeight: 800, color: C.text, letterSpacing: '-0.02em' }}>{title}</div>
        {description && (
          <div style={{ fontSize: 12.5, color: C.sub, marginTop: 4, lineHeight: 1.5 }}>{description}</div>
        )}
      </div>
      {right && <div style={{ flexShrink: 0 }}>{right}</div>}
    </div>
  )
}