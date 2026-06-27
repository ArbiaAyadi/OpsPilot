// Formate des heures (float) en chaine lisible "2d 4h", "45min", "6h"
export const fmtUptime = (h) => {
  if (!h || h <= 0) return '—'
  if (h < 1) return `${Math.round(h * 60)}min`
  const d = Math.floor(h/24), r = Math.floor(h%24)
  return d > 0 ? `${d}d ${r}h` : `${r}h`
}

// Formate des secondes en HH:MM:SS
export const fmtTime = (s) =>
  `${String(Math.floor(s/3600)).padStart(2,'0')}:${String(Math.floor((s%3600)/60)).padStart(2,'0')}:${String(s%60).padStart(2,'0')}`