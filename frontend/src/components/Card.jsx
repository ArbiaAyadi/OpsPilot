import { C } from '../utils/colors'

export function Card({ children, style = {}, glow }) {
  return (
    <div style={{
      background: C.card, borderRadius: 10,
      border: `1px solid ${glow ? glow+'50' : C.border}`,
      boxShadow: glow ? `0 0 20px ${glow}08` : 'none',
      ...style
    }}>
      {children}
    </div>
  )
}