import { useState, useEffect, useRef, useCallback } from 'react'

// Hook de connexion WebSocket avec reconnexion automatique (3s).
// handlerRef permet au composant appelant de brancher/debrancher
// son propre gestionnaire de message sans recreer la connexion.
export function useWebSocket(url) {
  const [connected, setConnected] = useState(false)
  const wsRef      = useRef(null)
  const retryRef   = useRef(null)
  const handlerRef = useRef(null)

  const connect = useCallback(() => {
    if (wsRef.current?.readyState === WebSocket.OPEN) return
    const ws = new WebSocket(url)
    wsRef.current = ws
    ws.onopen    = () => setConnected(true)
    ws.onclose   = () => { setConnected(false); retryRef.current = setTimeout(connect, 3000) }
    ws.onerror   = () => ws.close()
    ws.onmessage = (e) => { try { handlerRef.current?.(JSON.parse(e.data)) } catch {} }
  }, [url])

  useEffect(() => {
    connect()
    return () => { clearTimeout(retryRef.current); wsRef.current?.close() }
  }, [connect])

  const send = useCallback((data) => {
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.send(JSON.stringify(data))
  }, [])

  return { connected, send, handlerRef }
}