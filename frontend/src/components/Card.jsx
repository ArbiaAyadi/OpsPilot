import { C } from '../utils/colors'

export function Card({ children, style = {}, glow }) {
  return (
    <div style={{
      background: glow
        ? `linear-gradient(160deg, ${glow}0e 0%, ${C.card} 58%)`
        : C.cardGrad,
      borderRadius: C.radius,
      border: `1px solid ${glow ? glow + '55' : C.border}`,
      boxShadow: glow
        ? `0 0 0 1px ${glow}14, 0 8px 28px ${glow}12`
        : C.glowSoft,
      ...style,
    }}>
      {children}
    </div>
  )
}