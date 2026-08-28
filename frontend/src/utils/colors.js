/**
 * OpsPilot — Palette de couleurs centralisée v4
 *
 * ← MODIFIÉ (v3 -> v4) : refonte visuelle. Aucun renommage -- toutes les
 * clés existantes (bg, surface, card, border, borderHi, text, sub, muted,
 * blue, green, yellow, orange, red, purple, cyan) gardent EXACTEMENT leur
 * nom, donc aucune page n'est cassée par cette mise à jour. Seules leurs
 * valeurs bougent, plus quelques clés AJOUTÉES en fin de fichier.
 *
 * 1. PROFONDEUR DE SURFACES : bg/surface/card étaient très proches les uns
 *    des autres, ce qui écrasait la hiérarchie -- une carte posée sur le
 *    fond se distinguait à peine. Les trois niveaux sont maintenant
 *    nettement séparés : le fond recule, les cartes avancent.
 *
 * 2. ACCENT CYAN : le cyan existait déjà mais n'était utilisé quasiment
 *    nulle part. Il devient le second accent (après le bleu) pour les
 *    éléments actifs -- c'est ce qui donne à une interface produit son
 *    identité, plutôt qu'un bleu unique partout.
 *
 * 3. LISIBILITÉ : muted (#64748b) était un peu sombre pour des libellés
 *    sur fond très foncé. Remonté d'un cran.
 */
export const C = {
  // ── Fonds : trois niveaux nettement distincts ──────────────────────────
  bg:       '#050a14',   // fond application -- le plus sombre, recule
  surface:  '#0a1524',   // panneaux, barre latérale -- niveau intermédiaire
  card:     '#0e1b2e',   // cartes -- le plus clair des trois, avance

  border:   '#1c2f4a',   // bordure standard -- bleu profond, discret mais présent
  borderHi: '#2f5580',   // bordure accentuée (survol, sélection)

  // ── Texte ──────────────────────────────────────────────────────────────
  text:     '#f1f5f9',
  sub:      '#9fb3c8',   // légèrement plus clair que #94a3b8 -- meilleure lisibilité
  muted:    '#6b7f96',   // remonté depuis #64748b -- les libellés restaient trop discrets

  // ── Couleurs sémantiques (elles portent du sens, pas du style) ─────────
  blue:     '#3b82f6',
  green:    '#22c55e',
  yellow:   '#eab308',
  orange:   '#f97316',
  red:      '#ef4444',
  purple:   '#a855f7',
  cyan:     '#22d3ee',   // était #06b6d4 -- plus lumineux, tient mieux comme accent

  // ── AJOUTS v4 : utilitaires de style, jamais requis ────────────────────
  // Rien de ce qui suit n'est utilisé par les pages existantes -- valeurs
  // disponibles pour les composants mis à jour, sûres à ignorer ailleurs.
  accent:     '#22d3ee',
  accentSoft: 'rgba(34,211,238,0.10)',
  accentLine: 'rgba(34,211,238,0.35)',
  cardGrad:   'linear-gradient(160deg, #12243c 0%, #0e1b2e 62%)',
  glowSoft:   '0 1px 3px rgba(0,0,0,0.45), 0 10px 30px rgba(0,0,0,0.22)',
  radius:     14,
}