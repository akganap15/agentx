import { useState, useEffect, useRef } from 'react'
import { RetellWebClient } from 'retell-client-js-sdk'

export default function VoiceCallRetell({ onCallEnd }) {
  const [callState, setCallState] = useState('connecting')   // connecting | active | ended
  const [statusText, setStatusText] = useState('Connecting to Retell AI...')
  const [transcript, setTranscript] = useState([])
  const [error, setError] = useState(null)
  const [duration, setDuration] = useState(0)
  const [agentTalking, setAgentTalking] = useState(false)

  const clientRef = useRef(null)
  const timerRef = useRef(null)
  const transcriptEndRef = useRef(null)

  useEffect(() => {
    startCall()
    return () => cleanup()
  }, [])

  useEffect(() => {
    transcriptEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript])

  useEffect(() => {
    if (callState === 'active') {
      timerRef.current = setInterval(() => setDuration(d => d + 1), 1000)
    } else {
      clearInterval(timerRef.current)
    }
    return () => clearInterval(timerRef.current)
  }, [callState])

  const formatDuration = (s) => `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  async function startCall() {
    try {
      const res = await fetch('/api/v1/voice/retell/register-call', { method: 'POST' })
      if (!res.ok) {
        const data = await res.json()
        throw new Error(data.error || `Register call failed: ${res.status}`)
      }
      const { access_token } = await res.json()

      const client = new RetellWebClient()
      clientRef.current = client

      client.on('call_started', () => {
        setCallState('active')
        setStatusText('Connected')
      })

      client.on('call_ended', () => {
        setCallState('ended')
        setStatusText('Call ended')
        setTimeout(onCallEnd, 1200)
      })

      client.on('agent_start_talking', () => {
        setAgentTalking(true)
        setStatusText('Agent speaking...')
      })

      client.on('agent_stop_talking', () => {
        setAgentTalking(false)
        setStatusText('Listening...')
      })

      client.on('update', (update) => {
        const items = update?.transcript ?? update?.data?.transcript ?? []
        if (!items.length) return
        // Merge consecutive same-role entries into single bubbles
        const merged = []
        for (const t of items) {
          if (!t.content?.trim()) continue
          const role = t.role === 'agent' ? 'agent' : 'customer'
          const last = merged[merged.length - 1]
          if (last && last.role === role) {
            last.content += ' ' + t.content.trim()
          } else {
            merged.push({ id: merged.length, role, content: t.content.trim() })
          }
        }
        if (merged.length) setTranscript(merged)
      })

      client.on('error', (err) => {
        setError(String(err?.message || err))
        setCallState('ended')
        setTimeout(onCallEnd, 3000)
      })

      await client.startCall({ accessToken: access_token })

    } catch (err) {
      setError(err.message)
      setCallState('ended')
      setTimeout(onCallEnd, 3000)
    }
  }

  function cleanup() {
    clearInterval(timerRef.current)
    try { clientRef.current?.stopCall() } catch (_) {}
  }

  function handleEnd() {
    cleanup()
    onCallEnd()
  }

  const stateLabel = {
    connecting: 'Connecting...',
    active: agentTalking ? 'Agent speaking' : 'Listening',
    ended: 'Call ended',
  }[callState]

  const stateColor = {
    connecting: '#aaa',
    active: '#00c853',
    ended: '#E20074',
  }[callState]

  return (
    <div className="voice-overlay">
      <div className="voice-modal">

        {/* Header */}
        <div className="voice-header">
          <div className="voice-avatar">AP</div>
          <div className="voice-header-info">
            <div className="voice-biz-name">Alex's Plumbing Service</div>
            <div className="voice-status">
              <span className="voice-status-dot" style={{ background: stateColor }} />
              {stateLabel}
            </div>
          </div>
          <div className="voice-badges">
            <span className="voice-model-badge">Retell AI</span>
            {callState === 'active' && (
              <span className="voice-duration">{formatDuration(duration)}</span>
            )}
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="voice-error" onClick={() => setError(null)}>
            ⚠️ {error} — tap to dismiss
          </div>
        )}

        {/* Transcript */}
        <div className="voice-transcript">
          {callState === 'connecting' && !error && (
            <div className="voice-connecting">
              <div className="voice-spinner" />
              <p>Connecting to Retell AI...</p>
            </div>
          )}
          {callState === 'active' && transcript.length === 0 && !agentTalking && (
            <div className="voice-connecting">
              <div style={{ fontSize: 32 }}>🎙️</div>
              <p style={{ textAlign: 'center' }}>
                Connected — speak to start the conversation
              </p>
            </div>
          )}
          {callState === 'active' && transcript.length === 0 && agentTalking && (
            <div className="voice-msg agent">
              <div className="voice-bubble typing-bubble">
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
              </div>
            </div>
          )}
          {transcript.map((t) => (
            <div key={t.id} className={`voice-msg ${t.role}`}>
              <div className="voice-bubble">{t.content}</div>
            </div>
          ))}
          {agentTalking && transcript.length > 0 && (
            <div className="voice-msg agent">
              <div className="voice-bubble typing-bubble">
                <div className="thinking-dot" />
                <div className="thinking-dot" />
                <div className="thinking-dot" />
              </div>
            </div>
          )}
          <div ref={transcriptEndRef} />
        </div>

        {/* Controls */}
        <div className="voice-controls">
          <div className="voice-powered">Powered by Retell AI · Claude Agents</div>
          <button className="voice-end-btn" onClick={handleEnd}>
            🔴 End
          </button>
        </div>

      </div>
    </div>
  )
}
