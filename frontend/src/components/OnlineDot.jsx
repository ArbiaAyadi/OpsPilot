import { C } from '../utils/colors'

export function OnlineDot({ online, pulse = true }) {
  return (
    <span style={{
      width: 7, height: 7, borderRadius: '50%', display: 'inline-block',
      background: online ? C.green : C.red, flexShrink: 0,
      animation: online && pulse ? 'pulse 2s infinite' : 'none'
    }}/>
  )
}