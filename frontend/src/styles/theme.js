import { C } from '../utils/colors'

// Couleur de risque simple basee sur un pourcentage (0-100)
export const riskColor = (v) => v > 90 ? C.red : v > 75 ? C.orange : v > 50 ? C.yellow : C.green

// Statut a 3 niveaux generique : value, seuils warning/critical
// (peut etre inverse pour "plus petit = pire", ex: ZFS ARC hit rate)
export const statusOf = (value, warn, crit, inverse = false) => {
  if (value == null) return { label: 'N/A', color: C.muted }
  if (inverse) {
    if (value <= crit) return { label: 'CRITICAL', color: C.red }
    if (value <= warn) return { label: 'WARNING',  color: C.orange }
    return { label: 'OK', color: C.green }
  }
  if (value >= crit) return { label: 'CRITICAL', color: C.red }
  if (value >= warn) return { label: 'WARNING',  color: C.orange }
  return { label: 'OK', color: C.green }
}

// Couleur associee a une severite, en gerant a la fois les libelles
// anglais (CRITICAL/HIGH/MONITORING) et francais (CRITIQUE/IMPORTANT/SURVEILLANCE)
export const severityColor = (s = '') => ({
  CRITICAL: C.red, CRITIQUE: C.red,
  HIGH: C.orange, IMPORTANT: C.orange,
  MEDIUM: C.yellow, SURVEILLANCE: C.yellow, MONITORING: C.yellow,
  LOW: C.green, NORMAL: C.green,
})[s.toUpperCase()] || C.sub

// Normalise une severite (le LLM genere parfois en francais) vers le
// libelle anglais utilise par les filtres d'affichage.
export const normalizeSeverity = (s = '') => ({
  CRITIQUE:'CRITICAL', IMPORTANT:'HIGH', SURVEILLANCE:'MONITORING',
  CRITICAL:'CRITICAL', HIGH:'HIGH', MONITORING:'MONITORING', NORMAL:'NORMAL',
})[s.toUpperCase()] || s