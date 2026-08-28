import React from 'react'
/**
 * OpsPilot — Authentification
 *
 * Refonte visuelle complète : fond animé (diagramme réseau composé à la
 * main, pas un nuage de particules générique -- deux nœuds centraux et
 * leurs satellites, echo direct de la vraie topologie du produit : deux
 * nœuds Proxmox, chacun avec ses VMs), palette plus sombre, nom du projet
 * en Chakra Petch (lettres à angles coupés, lien visuel avec
 * circuits/infrastructure), boutons avec un vrai cadrage (bordure visible,
 * pas juste un aplat de couleur).
 *
 * La logique métier des vues login/signup est inchangée par rapport à la
 * version précédente -- seule la couche visuelle est reprise ici. Le flux
 * de réinitialisation de mot de passe a depuis été refondu : code à 6
 * chiffres saisi à la main plutôt qu'un lien avec token dans l'URL (voir
 * VueForgotPassword) -- plus de lecture d'URL au montage du tout.
 *
 * Nécessite framer-motion (npm install framer-motion) -- utilisée pour les
 * impulsions qui voyagent le long des connexions et les nœuds qui simulent
 * une anomalie détectée puis résolue. Respecte prefers-reduced-motion : le
 * diagramme reste visible mais statique si l'utilisateur l'a demandé.
 */
import { useState, useEffect, useRef } from 'react'
import { motion, useReducedMotion } from 'framer-motion'
import { C } from '../utils/colors'

// ══════════════════════════════════════════════════════════════════════════════
// Polices — Chakra Petch pour le nom du projet uniquement, utilisé avec
// retenue (voir le reste du fichier : Inter et JetBrains Mono partout
// ailleurs, déjà la typographie établie du reste de l'application).
// ══════════════════════════════════════════════════════════════════════════════
function InjecterPolices() {
  useEffect(() => {
    if (document.getElementById('opspilot-auth-fonts')) return
    const link = document.createElement('link')
    link.id  = 'opspilot-auth-fonts'
    link.rel = 'stylesheet'
    link.href = 'https://fonts.googleapis.com/css2?family=Chakra+Petch:wght@600;700&family=JetBrains+Mono:wght@400;500&display=swap'
    document.head.appendChild(link)
  }, [])
  return null
}

// ══════════════════════════════════════════════════════════════════════════════
// Fond animé — diagramme réseau composé, pas un nuage de particules
// ══════════════════════════════════════════════════════════════════════════════
// Deux "hubs" (nœuds hypervis eur, plus grands) avec leurs satellites (VMs,
// plus petits) -- écho direct de la vraie topologie du produit sans être
// littéral. Coordonnées dans un viewBox 1600x900, mises à l'échelle par
// preserveAspectRatio pour couvrir tout l'écran sans distorsion.
const HUBS = [
  { id: 'h1', x: 380,  y: 300 },
  { id: 'h2', x: 1240, y: 560 },
]
const SATELLITES = [
  { id: 's1',  x: 150,  y: 160,  hub: 'h1' },
  { id: 's2',  x: 560,  y: 150,  hub: 'h1' },
  { id: 's3',  x: 170,  y: 480,  hub: 'h1' },
  { id: 's4',  x: 470,  y: 520,  hub: 'h1' },
  { id: 's5',  x: 300,  y: 620,  hub: 'h1' },
  { id: 's6',  x: 1040, y: 340,  hub: 'h2' },
  { id: 's7',  x: 1460, y: 380,  hub: 'h2' },
  { id: 's8',  x: 1080, y: 720,  hub: 'h2' },
  { id: 's9',  x: 1500, y: 660,  hub: 'h2' },
  { id: 's10', x: 1360, y: 210,  hub: 'h2' },
]
// Quelques liens transverses en plus des liens hub->satellite, pour une
// texture de maillage plus riche qu'une simple étoile double.
const LIENS_TRANSVERSES = [
  ['s2', 's6'], ['s4', 'h2'], ['s6', 'h1'],
]

function cheminEntre(a, b) {
  return `M ${a.x} ${a.y} L ${b.x} ${b.y}`
}

function Pulse({ d, delai, duree, couleur }) {
  return (
    <motion.circle
      r={3.2}
      fill={couleur}
      style={{ offsetPath: `path("${d}")`, offsetRotate: '0deg' }}
      initial={{ offsetDistance: '0%', opacity: 0 }}
      animate={{ offsetDistance: '100%', opacity: [0, 1, 1, 0] }}
      transition={{ duration: duree, delay: delai, repeat: Infinity, ease: 'linear' }}
    />
  )
}

function NoeudAnomalie({ noeud, delai }) {
  return (
    <motion.circle
      cx={noeud.x} cy={noeud.y} r={4.5}
      fill="#2b3a56"
      animate={{ fill: ['#2b3a56', '#2b3a56', '#f59e0b', '#ef4444', '#f59e0b', '#2b3a56', '#2b3a56'] }}
      transition={{ duration: 9, delay: delai, repeat: Infinity, times: [0, 0.55, 0.65, 0.72, 0.8, 0.9, 1] }}
    />
  )
}

function FondReseau() {
  const reduitMotion = useReducedMotion()
  const tousLesNoeuds = [...HUBS, ...SATELLITES]
  const parId = Object.fromEntries(tousLesNoeuds.map(n => [n.id, n]))

  const liens = [
    ...SATELLITES.map(s => [s.hub, s.id]),
    ...LIENS_TRANSVERSES,
  ].map(([a, b]) => ({ a: parId[a], b: parId[b], d: cheminEntre(parId[a], parId[b]) }))

  // Impulsions seulement sur un sous-ensemble des liens -- une carte
  // saturée de mouvement partout deviendrait du bruit, pas une ambiance.
  const liensAvecPulse = liens.filter((_, i) => i % 2 === 0)
  // Nœuds qui simulent occasionnellement une anomalie détectée puis
  // résolue -- 3 seulement, décalés dans le temps pour ne jamais clignoter
  // ensemble.
  const noeudsAnomalie = [SATELLITES[2], SATELLITES[7], SATELLITES[4]]

  return (
    <svg
      viewBox="0 0 1600 900" preserveAspectRatio="xMidYMid slice"
      style={{ position: 'absolute', inset: 0, width: '100%', height: '100%', opacity: 0.4 }}
      aria-hidden="true"
    >
      {liens.map((l, i) => (
        <line key={i} x1={l.a.x} y1={l.a.y} x2={l.b.x} y2={l.b.y}
              stroke="#1c2740" strokeWidth={1.5} />
      ))}

      {!reduitMotion && liensAvecPulse.map((l, i) => (
        <Pulse key={i} d={l.d} delai={(i * 0.9) % 4} duree={3.5 + (i % 3)} couleur="#22d3ee" />
      ))}

      {SATELLITES.filter(s => !noeudsAnomalie.includes(s)).map(s => (
        <circle key={s.id} cx={s.x} cy={s.y} r={4.5} fill="#2b3a56" />
      ))}
      {HUBS.map(h => <circle key={h.id} cx={h.x} cy={h.y} r={9} fill="#2b3a56" stroke="#3b82f6" strokeWidth={1.5} />)}

      {!reduitMotion ? (
        noeudsAnomalie.map((n, i) => <NoeudAnomalie key={n.id} noeud={n} delai={i * 3.4} />)
      ) : (
        // ← Sans animation (prefers-reduced-motion), ces nœuds restent
        // rendus normalement -- jamais invisibles, juste statiques comme
        // tout le reste du diagramme.
        noeudsAnomalie.map(n => <circle key={n.id} cx={n.x} cy={n.y} r={4.5} fill="#2b3a56" />)
      )}
    </svg>
  )
}

// ══════════════════════════════════════════════════════════════════════════════
// Éléments de formulaire — bordure visible sur les boutons ("cadrage"),
// pas un simple aplat de couleur
// ══════════════════════════════════════════════════════════════════════════════
const style_champ = {
  width: '100%', padding: '11px 13px', borderRadius: 8,
  border: '1px solid #1c2740', background: '#050a14',
  color: '#e8edf5', fontSize: 13, outline: 'none', boxSizing: 'border-box',
  fontFamily: "'Inter', sans-serif",
}

const style_bouton_principal = {
  width: '100%', padding: '12px 0', borderRadius: 8,
  // ← MODIFIÉ : bleu vif aplati (#3b82f6) -> bleu profond du thème, avec
  // une bordure animée. La couleur de fond et la bordure sont désormais
  // pilotées par la classe CSS .opspilot-auth-btn-principal (voir le bloc
  // <style> plus bas) -- une bordure en dégradé mouvant ne peut pas se
  // décrire en style inline, elle a besoin de @keyframes.
  border: '1.5px solid transparent', color: '#fff',
  fontSize: 13, fontWeight: 700, cursor: 'pointer', marginTop: 4,
  boxShadow: '0 0 0 0 rgba(59,130,246,0)',
}

const style_lien = {
  color: '#3b82f6', textDecoration: 'none', fontSize: 12, cursor: 'pointer',
  background: 'none', border: 'none', padding: 0, fontFamily: "'Inter', sans-serif",
}

function LienBouton({ children, ...props }) {
  const reduitMotion = useReducedMotion()
  return (
    <motion.button
      type="button"
      style={style_lien}
      whileHover={reduitMotion ? {} : { opacity: 0.75 }}
      whileTap={reduitMotion ? {} : { scale: 0.97 }}
      transition={{ duration: 0.12 }}
      {...props}
    >
      {children}
    </motion.button>
  )
}

function Champ({ label, style, ...props }) {
  return (
    <div style={{ marginBottom: 14 }}>
      {/* ← MODIFIÉ (2e passe) : #7a8bab -> #3f5a78. Le premier ajustement
          (#5b7799) restait trop clair -- ces libellés sont des indications
          secondaires, ils ne doivent jamais rivaliser avec la valeur
          saisie juste en dessous. Bleu foncé, dans le ton du fond, tout
          en restant au-dessus du seuil de lisibilité. */}
      <label style={{ display: 'block', fontSize: 10.5, color: '#3f5a78', marginBottom: 6,
                       fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.04em' }}>
        {label}
      </label>
      {/* ← style et props.style fusionnés explicitement (pas juste
          juxtaposés) -- voir BoutonPrincipal ci-dessous pour l'incident
          exact que ce pattern évite. */}
      <input className="opspilot-auth-input" style={{ ...style_champ, ...style }} {...props} />
    </div>
  )
}

function BoutonPrincipal({ children, style, ...props }) {
  const reduitMotion = useReducedMotion()
  return (
    // ← CORRIGÉ : bug réel trouvé en testant -- chaque appel passait
    // style={{opacity: ...}}, et {...props} (qui contient CE style)
    // placé après style={style_bouton_principal} écrasait ENTIÈREMENT le
    // style par défaut au lieu de le compléter. Le bouton retombait sur
    // l'apparence par défaut du navigateur (blanc, sans padding) --
    // exactement ce qui apparaissait à l'écran. style et props sont
    // maintenant fusionnés explicitement, dans le bon ordre.
    // ← AJOUT : animation au survol/clic via motion.button, désactivée si
    // prefers-reduced-motion.
    <motion.button
      className="opspilot-auth-btn-principal"
      style={{ ...style_bouton_principal, ...style }}
      whileHover={reduitMotion ? {} : { scale: 1.015, boxShadow: '0 4px 18px rgba(59,130,246,0.35)' }}
      whileTap={reduitMotion ? {} : { scale: 0.98 }}
      transition={{ duration: 0.15 }}
      {...props}
    >
      {children}
    </motion.button>
  )
}

function BandeauErreur({ message }) {
  if (!message) return null
  return (
    <div style={{ padding: '9px 12px', borderRadius: 7, background: 'rgba(239,68,68,0.1)',
                  border: '1px solid rgba(239,68,68,0.35)', color: '#ef4444', fontSize: 12,
                  marginBottom: 14, lineHeight: 1.5 }}>
      {message}
    </div>
  )
}

function BandeauSucces({ message }) {
  if (!message) return null
  return (
    <div style={{ padding: '9px 12px', borderRadius: 7, background: 'rgba(34,197,94,0.1)',
                  border: '1px solid rgba(34,197,94,0.35)', color: '#22c55e', fontSize: 12,
                  marginBottom: 14, lineHeight: 1.5 }}>
      {message}
    </div>
  )
}

async function appelAuth(chemin, body) {
  const r = await fetch(chemin, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(body),
  })
  const data = await r.json().catch(() => ({}))
  if (!r.ok) throw new Error(data.detail || 'Something went wrong. Please try again.')
  return data
}

// ── LOGIN ────────────────────────────────────────────────────────────────
function VueLogin({ onSwitch, onAuthenticated }) {
  const [email, setEmail]       = useState('')
  const [password, setPassword] = useState('')
  const [erreur, setErreur]     = useState(null)
  const [chargement, setChargement] = useState(false)

  const soumettre = async (e) => {
    e.preventDefault()
    if (chargement) return
    setErreur(null)
    setChargement(true)
    try {
      const data = await appelAuth('/api/auth/login', { email, password })
      onAuthenticated(data.user)
    } catch (err) {
      setErreur(err.message)
    } finally {
      setChargement(false)
    }
  }

  return (
    <form onSubmit={soumettre}>
      <BandeauErreur message={erreur} />
      <Champ label="EMAIL" type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus />
      <Champ label="PASSWORD" type="password" value={password} onChange={e => setPassword(e.target.value)} required />
      <BoutonPrincipal type="submit" disabled={chargement} style={{ opacity: chargement ? 0.6 : 1 }}>
        {chargement ? 'Signing in...' : 'Sign in'}
      </BoutonPrincipal>
      <div style={{ display: 'flex', justifyContent: 'space-between', marginTop: 18 }}>
        <LienBouton onClick={() => onSwitch('signup')}>Create an account</LienBouton>
        <LienBouton onClick={() => onSwitch('forgot')}>Forgot password?</LienBouton>
      </div>
    </form>
  )
}

// ── SIGNUP ───────────────────────────────────────────────────────────────
function VueSignup({ onSwitch, onAuthenticated }) {
  const [nom, setNom]             = useState('')
  const [email, setEmail]         = useState('')
  const [password, setPassword]   = useState('')
  const [inviteCode, setInviteCode] = useState('')
  const [erreur, setErreur]       = useState(null)
  const [chargement, setChargement] = useState(false)

  const soumettre = async (e) => {
    e.preventDefault()
    if (chargement) return
    setErreur(null)
    setChargement(true)
    try {
      const data = await appelAuth('/api/auth/signup', { nom, email, password, invite_code: inviteCode })
      onAuthenticated(data.user)
    } catch (err) {
      setErreur(err.message)
    } finally {
      setChargement(false)
    }
  }

  return (
    <form onSubmit={soumettre}>
      <BandeauErreur message={erreur} />
      <Champ label="FULL NAME" type="text" value={nom} onChange={e => setNom(e.target.value)} required autoFocus />
      <Champ label="EMAIL" type="email" value={email} onChange={e => setEmail(e.target.value)} required />
      <Champ label="PASSWORD" type="password" value={password} onChange={e => setPassword(e.target.value)} required />
      <div style={{ fontSize: 10, color: '#5b6b85', marginTop: -8, marginBottom: 14, lineHeight: 1.5 }}>
        At least 12 characters. Length matters more than symbols — no forced complexity rules.
      </div>
      <Champ label="INVITE CODE" type="text" value={inviteCode} onChange={e => setInviteCode(e.target.value)}
             placeholder="Provided by your team admin" required />
      <BoutonPrincipal type="submit" disabled={chargement} style={{ opacity: chargement ? 0.6 : 1 }}>
        {chargement ? 'Creating account...' : 'Create account'}
      </BoutonPrincipal>
      <div style={{ textAlign: 'center', marginTop: 18 }}>
        <LienBouton onClick={() => onSwitch('login')}>Already have an account? Sign in</LienBouton>
      </div>
    </form>
  )
}

// ── FORGOT PASSWORD ──────────────────────────────────────────────────────
// ← REFONTE : tout se passe maintenant sur cette seule page, sans lien à
// suivre dans l'email. Étape 1 : email -> code envoyé. Étape 2 (même vue,
// affichée juste après) : code + nouveau mot de passe saisis ensemble et
// soumis en un seul appel à /api/auth/reset-password -- ce endpoint
// acceptait déjà "token" comme une chaîne quelconque, donc un code tapé
// à la main fonctionne sans aucun changement côté backend.
function VueForgotPassword({ onSwitch }) {
  const [email, setEmail]     = useState('')
  const [envoye, setEnvoye]   = useState(false)
  const [erreur, setErreur]   = useState(null)
  const [chargement, setChargement] = useState(false)

  const [code, setCode]             = useState('')
  const [password, setPassword]     = useState('')
  const [termine, setTermine]       = useState(false)
  const [erreur2, setErreur2]       = useState(null)
  const [chargement2, setChargement2] = useState(false)

  const soumettreEmail = async (e) => {
    e.preventDefault()
    if (chargement) return
    setErreur(null)
    setChargement(true)
    try {
      await appelAuth('/api/auth/forgot-password', { email })
      setEnvoye(true)
    } catch (err) {
      setErreur(err.message)
    } finally {
      setChargement(false)
    }
  }

  const soumettreCode = async (e) => {
    e.preventDefault()
    if (chargement2) return
    setErreur2(null)
    setChargement2(true)
    try {
      await appelAuth('/api/auth/reset-password', { token: code.trim(), new_password: password })
      setTermine(true)
    } catch (err) {
      setErreur2(err.message)
    } finally {
      setChargement2(false)
    }
  }

  if (termine) {
    return (
      <div>
        <BandeauSucces message="Password updated. You can now sign in with your new password." />
        <BoutonPrincipal onClick={() => onSwitch('login')}>Go to sign in</BoutonPrincipal>
      </div>
    )
  }

  if (envoye) {
    return (
      <form onSubmit={soumettreCode}>
        <BandeauSucces message="If that email is registered, a 6-digit code has been sent. Check your inbox." />
        <div style={{ fontSize: 12, color: '#9db0cc', margin: '16px 0', lineHeight: 1.5 }}>
          Enter the code from your email, along with your new password.
        </div>
        <BandeauErreur message={erreur2} />
        <Champ
          label="RESET CODE" type="text" inputMode="numeric" placeholder="000000"
          value={code} onChange={e => setCode(e.target.value.replace(/\D/g, '').slice(0, 6))}
          maxLength={6} required autoFocus
          style={{ letterSpacing: '0.35em', fontSize: 18, textAlign: 'center', fontFamily: 'monospace' }}
        />
        <Champ label="NEW PASSWORD" type="password" value={password} onChange={e => setPassword(e.target.value)} required />
        <div style={{ fontSize: 10, color: '#5b6b85', marginTop: -8, marginBottom: 14 }}>
          At least 12 characters. Length matters more than symbols.
        </div>
        <BoutonPrincipal type="submit" disabled={chargement2} style={{ opacity: chargement2 ? 0.6 : 1 }}>
          {chargement2 ? 'Updating...' : 'Update password'}
        </BoutonPrincipal>
        <div style={{ textAlign: 'center', marginTop: 18 }}>
          <LienBouton onClick={() => onSwitch('login')}>Back to sign in</LienBouton>
        </div>
      </form>
    )
  }

  return (
    <form onSubmit={soumettreEmail}>
      <div style={{ fontSize: 12, color: '#9db0cc', marginBottom: 16, lineHeight: 1.5 }}>
        Enter your email and we'll send you a 6-digit code to reset your password.
      </div>
      <BandeauErreur message={erreur} />
      <Champ label="EMAIL" type="email" value={email} onChange={e => setEmail(e.target.value)} required autoFocus />
      <BoutonPrincipal type="submit" disabled={chargement} style={{ opacity: chargement ? 0.6 : 1 }}>
        {chargement ? 'Sending...' : 'Send reset code'}
      </BoutonPrincipal>
      <div style={{ textAlign: 'center', marginTop: 18 }}>
        <LienBouton onClick={() => onSwitch('login')}>Back to sign in</LienBouton>
      </div>
    </form>
  )
}

// ── RESET PASSWORD ───────────────────────────────────────────────────────
// ← RETIRÉ : VueResetPassword, qui dépendait d'un token lu dans l'URL.
// L'étape "code + nouveau mot de passe" vit maintenant directement dans
// VueForgotPassword ci-dessus (affichée après l'envoi de l'email), plus
// besoin d'une vue séparée déclenchée par un lien.

const TITRES = {
  login:  { titre: 'Sign in to OpsPilot',    sous: 'Infrastructure monitoring for your team' },
  signup: { titre: 'Create your account',    sous: 'You\'ll need an invite code from your team admin' },
  forgot: { titre: 'Reset your password',    sous: '' },
}

export function PageAuth({ onAuthenticated }) {
  const [vue, setVue] = useState('login')

  const infos = TITRES[vue]

  return (
    // ← MODIFIÉ : fond '#030712' (presque noir, neutre) -> même dégradé
    // bleu que le reste de l'application. L'animation de réseau
    // (FondReseau) est CONSERVÉE telle quelle -- seule la couleur
    // derrière elle change, ce qui la rend d'ailleurs plus visible.
    <div style={{ position: 'relative', minHeight: '100vh', display: 'flex', alignItems: 'center',
                  justifyContent: 'center', overflow: 'hidden', padding: 20,
                  background:
                    'radial-gradient(1100px 700px at 18% -6%, rgba(37,99,235,0.20) 0%, transparent 60%),' +
                    'radial-gradient(900px 640px at 104% 106%, rgba(34,211,238,0.13) 0%, transparent 58%),' +
                    'linear-gradient(168deg, #0a1a30 0%, #071426 48%, #050f1e 100%)' }}>
      <InjecterPolices />
      <style>{`
        .opspilot-auth-input:focus { border-color: #3b82f6 !important; box-shadow: 0 0 0 3px rgba(59,130,246,0.15); }
        /* ← Bouton principal : fond bleu profond + bordure animée.
           Technique : deux couches de fond superposées -- la première
           (padding-box) peint l'intérieur en bleu foncé, la seconde
           (border-box) peint la BORDURE avec un dégradé qu'on fait
           défiler. C'est la seule façon d'animer une bordure en dégradé
           en CSS pur, sans élément supplémentaire dans le DOM. */
        .opspilot-auth-btn-principal {
          background:
            linear-gradient(#132a4a, #16325a) padding-box,
            linear-gradient(90deg, #1e3a8a, #3b82f6, #22d3ee, #3b82f6, #1e3a8a) border-box !important;
          background-size: 100% 100%, 300% 100% !important;
          animation: opspilot-bordure 4s linear infinite;
        }
        @keyframes opspilot-bordure {
          from { background-position: 0 0, 0% 0; }
          to   { background-position: 0 0, 300% 0; }
        }
        .opspilot-auth-btn-principal:hover {
          background:
            linear-gradient(#17376b, #1b3d6b) padding-box,
            linear-gradient(90deg, #1e3a8a, #3b82f6, #22d3ee, #3b82f6, #1e3a8a) border-box !important;
          background-size: 100% 100%, 300% 100% !important;
          animation-duration: 1.6s;
        }
        .opspilot-auth-btn-principal:active { filter: brightness(0.92); }
        .opspilot-auth-btn-principal:focus-visible { outline: 2px solid #67e8f9; outline-offset: 3px; }
        /* Accessibilité : une animation continue peut gêner certaines
           personnes -- respectée par le système, la bordure devient fixe. */
        @media (prefers-reduced-motion: reduce) {
          .opspilot-auth-btn-principal { transition: none !important; animation: none !important; }
        }
      `}</style>

      <FondReseau />

      {/* Voile pour garder le fond en simple ambiance, jamais en
          compétition avec le formulaire pour l'attention. */}
      {/* ← ALLÉGÉ : le voile montait jusqu'à 92% d'opacité sur les bords,
          ce qui écrasait presque entièrement le fond -- d'où l'impression
          de page noire. Ramené à 30/62%, et teinté bleu plutôt que
          quasi-noir : le dégradé et l'animation restent visibles, le
          formulaire garde malgré tout le premier plan. */}
      <div style={{ position: 'absolute', inset: 0,
                    background: 'radial-gradient(circle at 50% 45%, rgba(5,15,30,0.30) 0%, rgba(5,15,30,0.62) 70%)' }} />

      <motion.div
        initial={{ opacity: 0, y: 14 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.5, ease: 'easeOut' }}
        style={{ position: 'relative', width: '100%', maxWidth: 400 }}
      >
        <div style={{ textAlign: 'center', marginBottom: 32 }}>
          <div style={{ fontFamily: "'Chakra Petch', sans-serif", fontSize: 40, fontWeight: 700,
                        color: '#e8edf5', letterSpacing: '0.01em', lineHeight: 1 }}>
            OpsPilot
          </div>
          <div style={{ fontSize: 11.5, color: '#5b6b85', marginTop: 8,
                        fontFamily: "'JetBrains Mono', monospace", letterSpacing: '0.06em' }}>
            AI-POWERED INFRASTRUCTURE MONITORING
          </div>
        </div>

        <div style={{ background: 'rgba(10,15,28,0.92)', border: '1px solid #1c2740', borderRadius: 16,
                      padding: 30, backdropFilter: 'blur(6px)' }}>
          <div style={{ marginBottom: 22 }}>
            <div style={{ fontSize: 17, fontWeight: 700, color: '#e8edf5', fontFamily: "'Inter', sans-serif" }}>
              {infos.titre}
            </div>
            {infos.sous && <div style={{ fontSize: 12, color: '#5b6b85', marginTop: 4 }}>{infos.sous}</div>}
          </div>

          {vue === 'login'  && <VueLogin  onSwitch={setVue} onAuthenticated={onAuthenticated} />}
          {vue === 'signup' && <VueSignup onSwitch={setVue} onAuthenticated={onAuthenticated} />}
          {vue === 'forgot' && <VueForgotPassword onSwitch={setVue} />}
          {vue === 'reset'  && <VueResetPassword token={resetToken} onSwitch={setVue} />}
        </div>
      </motion.div>
    </div>
  )
}