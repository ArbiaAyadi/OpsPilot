import { useState, useEffect } from 'react'
import { C } from '../utils/colors'
import { Card } from './Card'

/**
 * TokenBudget — consommation quotidienne réelle du budget LLM.
 *
 * Autonome : interroge /api/status lui-même (route déjà existante et déjà
 * protégée par l'authentification, cf. agent/routes.py) et se rafraîchit
 * toutes les 30s. Aucune prop à lui passer -- il suffit de l'importer et
 * de poser <TokenBudget/>, ce qui évite de faire descendre une nouvelle
 * donnée depuis App.jsx à travers PageDashboard juste pour cette carte.
 *
 * Mise en forme calquée sur les 3 cartes indicateurs déjà présentes en
 * haut de PageDashboard (COROSYNC/QUORUM, AI ANOMALY SCORE, CLUSTER
 * UPTIME) : même Card, même padding, même pastille de 8px, même libellé
 * mono en majuscules, même barre de 2px que la carte AI -- elle se lit
 * comme un 4e indicateur du même groupe, pas comme un élément rapporté.
 *
 * Chiffres RÉELS rapportés par l'API Groq sur chaque appel (champ
 * response.usage.total_tokens), pas une estimation -- voir
 * agent/groq_client.py.
 *
 * Aucun stockage navigateur -- tout vit dans l'état React, comme le reste
 * de l'app.
 */
export function TokenBudget() {
  const [donnees, setDonnees]       = useState(null)
  const [chargement, setChargement] = useState(true)

  useEffect(() => {
    let annule = false

    const charger = async () => {
      try {
        const r = await fetch('/api/status')
        if (!r.ok) throw new Error(`HTTP ${r.status}`)
        const j = await r.json()
        if (!annule) setDonnees(j)
      } catch {
        // Silencieux : une coupure réseau ponctuelle ne doit pas faire
        // clignoter la carte toutes les 30s. On garde la dernière valeur
        // connue affichée et on retente au cycle suivant.
      } finally {
        if (!annule) setChargement(false)
      }
    }

    charger()
    const timer = setInterval(charger, 30000)
    return () => { annule = true; clearInterval(timer) }
  }, [])

  const limite   = donnees?.tokens_limite_jour
  const utilises = donnees?.tokens_utilises_jour ?? 0
  const chat     = donnees?.tokens_utilises_chat ?? 0
  const pct      = donnees?.tokens_pct_utilise   ?? 0
  // ← AJOUT : fournisseurs de secours (Mistral, Ollama...). Chacun a son
  // propre quota, indépendant de celui de Groq -- sans cet affichage, un
  // basculement resterait invisible depuis le tableau de bord, et on ne
  // saurait pas non plus où en est le budget du secours.
  const secours  = donnees?.fournisseurs_secours ?? []
  const actif    = donnees?.fournisseur_actif ?? 'groq'

  // ← Rend TOUJOURS une carte de même gabarit, jamais null : elle occupe
  // une cellule d'une grille à 4 colonnes dans PageDashboard --
  // disparaître laisserait un trou visible à côté des 3 autres
  // indicateurs. Un état explicite ("—") est plus lisible qu'un vide, et
  // couvre aussi le cas d'un backend pas encore à jour (champs absents
  // de /api/status).
  const indisponible = chargement || limite == null

  // Seuils alignés sur le backend : la réserve "incidents CRITIQUE
  // uniquement" s'active à 20 000 tokens restants (agent/surveillance.py),
  // soit 90% d'un quota de 200 000 -- l'affichage passe au rouge AVANT ce
  // point, pour prévenir plutôt que constater.
  const couleur = indisponible ? C.muted : pct >= 85 ? C.red : pct >= 60 ? C.orange : C.green
  const libelle = indisponible ? '—'     : pct >= 85 ? 'LOW' : pct >= 60 ? 'MODERATE' : 'HEALTHY'

  const compact = (n) => n >= 1000 ? `${(n / 1000).toFixed(0)}k` : `${n}`

  return (
    <Card style={{ padding: '12px 16px', display: 'flex', alignItems: 'center', gap: 12 }}>
      <div style={{ width: 8, height: 8, borderRadius: '50%', background: couleur, flexShrink: 0 }}/>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 12, color: C.muted, fontFamily: 'JetBrains Mono, monospace', letterSpacing: '0.08em', marginBottom: 2 }}>
          DAILY AI BUDGET
        </div>
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
          <div style={{ fontSize: 14, fontWeight: 700, color: couleur }}>{libelle}</div>
          <div style={{ fontSize: 16, fontWeight: 800, color: couleur, fontFamily: 'JetBrains Mono, monospace' }}>
            {indisponible ? '—' : `${pct}%`}
          </div>
        </div>
        <div style={{ height: 2, background: C.border, borderRadius: 1, marginTop: 6 }}>
          <div style={{
            height: '100%',
            width: `${Math.min(100, Math.max(0, indisponible ? 0 : pct))}%`,
            background: couleur,
            borderRadius: 1,
            transition: 'width 1s ease',
          }}/>
        </div>
        <div style={{ fontSize: 10, color: C.muted, marginTop: 4, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
          {indisponible
            ? 'usage data unavailable'
            : `${compact(utilises)} monitoring · ${compact(chat)} assistant`}
        </div>
        {/* Fournisseurs de secours : affichés seulement s'il y en a de
            configurés, pour ne rien encombrer dans le cas courant. */}
        {!indisponible && secours.length > 0 && (
          <div style={{ fontSize: 10, color: C.muted, marginTop: 2, whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis' }}>
            {secours.map((s) => `${s.nom} ${compact(s.tokens_utilises)}`).join(' · ')}
          </div>
        )}
        {/* Bascule en cours : information importante, mise en évidence --
            l'agent tourne sur un secours, pas sur le fournisseur principal. */}
        {!indisponible && actif !== 'groq' && (
          <div style={{ fontSize: 10, color: C.blue, marginTop: 3, fontWeight: 700 }}>
            ⇄ running on {actif}
          </div>
        )}
      </div>
    </Card>
  )
}