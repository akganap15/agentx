import { useState, useEffect, useRef, useCallback } from 'react'
import './VoiceCall.css'

const SYSTEM_PROMPT = `You are a friendly and professional AI voice assistant for Andy Plumbing, a residential plumbing service in Austin, TX.

## Your job
Help callers book appointments or get help with plumbing issues. Services include pipe repair, drain cleaning, water heater install/repair, leak detection, and emergency callouts.

## Appointment booking — collect ALL of these before confirming:
1. Customer's full name
2. Service address (street, city)
3. Description of the problem (what's happening, how long, any urgency)
4. Preferred date AND time (offer options: morning 8am-12pm, afternoon 12pm-5pm, or specific time)
5. Best callback number (confirm or ask if different from caller ID)

Do NOT say "I've booked your appointment" or end the call until you have all 5 items above. If the caller skips one, ask for it before moving on.

## After collecting everything:
Read back a full summary: "Just to confirm — I have [name] at [address] on [date] at [time] for [issue]. We'll call [number] to confirm. Does that all sound right?"

Only say goodbye after the caller confirms the summary is correct.

## Emergency calls (burst pipe, flooding, no hot water):
- Immediately tell them to shut off the main water valve
- Prioritize getting their address first
- Tell them a technician can be there within 2 hours
- Still collect name and callback number

## Voice rules:
- 1-2 short sentences per turn — this is a phone call
- Ask exactly ONE question per turn
- Never ask two things at once
- Be warm, calm, and efficient`

// ── Audio helpers ────────────────────────────────────────────

function float32ToPcm16(float32) {
  const buf = new ArrayBuffer(float32.length * 2)
  const view = new DataView(buf)
  for (let i = 0; i < float32.length; i++) {
    const s = Math.max(-1, Math.min(1, float32[i]))
    view.setInt16(i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true)
  }
  return new Uint8Array(buf)
}

function toBase64(bytes) {
  let binary = ''
  for (let i = 0; i < bytes.length; i++) binary += String.fromCharCode(bytes[i])
  return btoa(binary)
}

function pcm16Base64ToFloat32(b64) {
  const binary = atob(b64)
  const bytes = new Uint8Array(binary.length)
  for (let i = 0; i < binary.length; i++) bytes[i] = binary.charCodeAt(i)
  const pcm16 = new Int16Array(bytes.buffer)
  const float32 = new Float32Array(pcm16.length)
  for (let i = 0; i < pcm16.length; i++) {
    float32[i] = pcm16[i] / (pcm16[i] < 0 ? 0x8000 : 0x7fff)
  }
  return float32
}

function msLabel(ms) {
  if (ms === null || ms === undefined) return '—'
  return ms < 1000 ? `${ms}ms` : `${(ms / 1000).toFixed(1)}s`
}

function latencyColor(ms) {
  if (ms === null || ms === undefined) return '#9aa3b2'
  if (ms < 400)  return '#00c853'
  if (ms < 800)  return '#ffab00'
  return '#d32f2f'
}

// ── Component ────────────────────────────────────────────────

export default function VoiceCall({ onCallEnd }) {
  const [callState, setCallState]   = useState('connecting')
  const [statusText, setStatusText] = useState('Connecting...')
  const [transcript, setTranscript] = useState([])
  const [liveText, setLiveText]     = useState('')
  const [duration, setDuration]     = useState(0)
  const [error, setError]           = useState(null)
  const [latencyHistory, setLatencyHistory] = useState([])  // [{vad, ttfa, stream, turn}]
  const [showLatency, setShowLatency]       = useState(true)

  const wsRef           = useRef(null)
  const audioCtxRef     = useRef(null)
  const processorRef    = useRef(null)
  const streamRef       = useRef(null)
  const nextPlayTimeRef = useRef(0)
  const timerRef        = useRef(null)
  const endingRef       = useRef(false)
  const messagesEndRef  = useRef(null)
  const aiSpeakingRef   = useRef(false)   // gate: AI currently speaking
  const micActiveRef    = useRef(false)   // gate: user has tapped to speak

  // Latency timing refs (reset each turn)
  const t = useRef({
    speechStarted: null,   // when VAD detects speech start
    speechStopped: null,   // when VAD detects speech end
    firstAudioDelta: null, // when first audio byte arrives
    lastAudioDelta: null,  // when last audio byte arrives
    responseDone: null,    // when response.done fires
  })

  const addLine = (role, text) =>
    setTranscript(prev => [...prev, { role, text, ts: new Date() }])

  const recordTurn = useCallback(() => {
    const { speechStarted, speechStopped, firstAudioDelta, lastAudioDelta, responseDone } = t.current
    if (!speechStopped) return

    const now = performance.now()
    const entry = {
      ts: new Date(),
      vad:    speechStarted && speechStopped
                ? Math.round(speechStopped - speechStarted) : null,
      ttfa:   speechStopped && firstAudioDelta
                ? Math.round(firstAudioDelta - speechStopped) : null,
      stream: firstAudioDelta && lastAudioDelta
                ? Math.round(lastAudioDelta - firstAudioDelta) : null,
      turn:   speechStopped && (responseDone || now)
                ? Math.round((responseDone || now) - speechStopped) : null,
    }
    setLatencyHistory(prev => [entry, ...prev].slice(0, 8))
    console.table({
      'VAD (speech duration)': entry.vad  != null ? `${entry.vad}ms`  : '—',
      'TTFA (speech→audio)':   entry.ttfa != null ? `${entry.ttfa}ms` : '—',
      'Stream (audio length)': entry.stream != null ? `${entry.stream}ms` : '—',
      'Turn (total)':          entry.turn  != null ? `${entry.turn}ms`  : '—',
    })
    // Reset for next turn
    t.current = { speechStarted: null, speechStopped: null, firstAudioDelta: null, lastAudioDelta: null, responseDone: null }
  }, [])

  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [transcript, liveText])

  const cleanup = useCallback(() => {
    clearInterval(timerRef.current)
    processorRef.current?.disconnect()
    streamRef.current?.getTracks().forEach(tk => tk.stop())
    audioCtxRef.current?.close()
    if (wsRef.current?.readyState === WebSocket.OPEN) wsRef.current.close()
  }, [])

  const endCall = useCallback(() => {
    if (endingRef.current) return
    endingRef.current = true
    setCallState('ended')
    setStatusText('Call ended')
    cleanup()
    setTimeout(() => onCallEnd?.(), 800)
  }, [cleanup, onCallEnd])

  const playChunk = useCallback((base64Audio) => {
    const ctx = audioCtxRef.current
    if (!ctx) return
    const float32 = pcm16Base64ToFloat32(base64Audio)
    const buffer = ctx.createBuffer(1, float32.length, 24000)
    buffer.copyToChannel(float32, 0)
    const source = ctx.createBufferSource()
    source.buffer = buffer
    source.connect(ctx.destination)
    const startAt = Math.max(nextPlayTimeRef.current, ctx.currentTime + 0.01)
    source.start(startAt)
    nextPlayTimeRef.current = startAt + buffer.duration
  }, [])

  const handleEvent = useCallback((event) => {
    switch (event.type) {

      case 'session.created':
        wsRef.current?.send(JSON.stringify({
          type: 'session.update',
          session: {
            instructions: SYSTEM_PROMPT,
            voice: 'shimmer',
            turn_detection: {
              type: 'server_vad',
              threshold: 0.5,
              prefix_padding_ms: 300,
              silence_duration_ms: 600,
            },
            input_audio_transcription: { model: 'whisper-1' },
            modalities: ['audio', 'text'],
          },
        }))
        wsRef.current?.send(JSON.stringify({
          type: 'conversation.item.create',
          item: { type: 'message', role: 'user', content: [{ type: 'input_text', text: 'Hello' }] },
        }))
        wsRef.current?.send(JSON.stringify({ type: 'response.create' }))
        setCallState('speaking')
        setStatusText('Agent speaking...')
        break

      case 'input_audio_buffer.speech_started':
        t.current.speechStarted = performance.now()
        setLiveText('')
        setStatusText('Listening...')
        setCallState('listening')
        nextPlayTimeRef.current = audioCtxRef.current?.currentTime ?? 0
        break

      case 'input_audio_buffer.speech_stopped':
        t.current.speechStopped = performance.now()
        micActiveRef.current = false   // stop sending audio — VAD detected end of speech
        setCallState('thinking')
        setStatusText('Processing...')
        break

      case 'conversation.item.input_audio_transcription.delta':
        setLiveText(prev => prev + (event.delta || ''))
        break

      case 'conversation.item.input_audio_transcription.completed': {
        const said = event.transcript?.trim()
        if (said) { addLine('customer', said); setLiveText('') }
        break
      }

      case 'response.audio.delta':
        if (event.delta) {
          if (!t.current.firstAudioDelta) t.current.firstAudioDelta = performance.now()
          t.current.lastAudioDelta = performance.now()
          playChunk(event.delta)
        }
        aiSpeakingRef.current = true
        setCallState('speaking')
        setStatusText('Agent speaking...')
        break

      case 'response.audio_transcript.done':
        if (event.transcript?.trim()) addLine('agent', event.transcript.trim())
        break

      case 'response.done':
        t.current.responseDone = performance.now()
        aiSpeakingRef.current = false
        micActiveRef.current = false
        setCallState('waiting')
        setStatusText('Tap mic to speak')
        recordTurn()
        break

      case 'error': {
        const msg = event.error?.message || ''
        // Suppress transient "active response in progress" — harmless race condition
        if (msg.includes('active response')) { console.warn('Realtime:', msg); break }
        console.error('Realtime error:', event.error)
        setError(msg || 'Realtime API error')
        break
      }

      default:
        break
    }
  }, [playChunk, recordTurn])

  useEffect(() => {
    let cancelled = false

    async function connect() {
      try {
        const wsUrl = `ws://${window.location.host}/api/v1/voice/ws`
        const ws = new WebSocket(wsUrl)
        wsRef.current = ws

        ws.onmessage = (e) => { try { handleEvent(JSON.parse(e.data)) } catch {} }
        ws.onerror   = () => setError('WebSocket connection failed')
        ws.onclose   = () => { if (!endingRef.current) setStatusText('Disconnected') }

        await new Promise((resolve, reject) => {
          ws.onopen  = resolve
          ws.onerror = reject
          setTimeout(() => reject(new Error('WebSocket timeout')), 8000)
        })
        if (cancelled) { ws.close(); return }

        const stream = await navigator.mediaDevices.getUserMedia({
          audio: {
            echoCancellation: true,
            noiseSuppression: true,
            autoGainControl: true,
          },
        })
        streamRef.current = stream
        if (cancelled) { stream.getTracks().forEach(tk => tk.stop()); ws.close(); return }

        const ctx = new AudioContext({ sampleRate: 24000 })
        audioCtxRef.current = ctx
        nextPlayTimeRef.current = ctx.currentTime

        const source = ctx.createMediaStreamSource(stream)
        const processor = ctx.createScriptProcessor(4096, 1, 1)
        processorRef.current = processor

        processor.onaudioprocess = (e) => {
          if (ws.readyState !== WebSocket.OPEN) return
          if (!micActiveRef.current || aiSpeakingRef.current) return
          const pcm = float32ToPcm16(e.inputBuffer.getChannelData(0))
          ws.send(JSON.stringify({ type: 'input_audio_buffer.append', audio: toBase64(pcm) }))
        }

        source.connect(processor)
        processor.connect(ctx.destination)
        timerRef.current = setInterval(() => setDuration(d => d + 1), 1000)

      } catch (err) {
        if (!cancelled) {
          console.error('Connect failed:', err)
          setError(err.message)
          setCallState('ended')
          setStatusText('Connection failed')
        }
      }
    }

    connect()
    return () => { cancelled = true; cleanup() }
  }, [handleEvent, cleanup]) // eslint-disable-line

  const startSpeaking = useCallback(() => {
    if (callState !== 'waiting' || aiSpeakingRef.current) return
    micActiveRef.current = true
    setCallState('listening')
    setStatusText('Listening...')
  }, [callState])

  const formatDuration = (s) =>
    `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  const isListening  = callState === 'listening'
  const isSpeaking   = callState === 'speaking'
  const isThinking   = callState === 'thinking'
  const isConnecting = callState === 'connecting'
  const isWaiting    = callState === 'waiting'

  const dotClass = isListening ? 'listening'
    : isSpeaking   ? 'speaking'
    : isThinking   ? 'thinking'
    : isConnecting ? 'connecting'
    : callState === 'ended' ? 'ended' : 'waiting'

  const latest = latencyHistory[0]

  return (
    <div className="vc-overlay">
      <div className="vc-modal">

        {/* Header */}
        <div className="vc-header">
          <div className="vc-header-left">
            <div className="vc-avatar">AP</div>
            <div>
              <div className="vc-business">Andy Plumbing</div>
              <div className="vc-status-row">
                <span className={`vc-state-dot ${dotClass}`} />
                <span className="vc-state-label">{statusText}</span>
              </div>
            </div>
          </div>
          <div className="vc-header-right">
            <div className="vc-model-badge">GPT-4o mini Realtime</div>
            <div className="vc-duration">{formatDuration(duration)}</div>
          </div>
        </div>

        {/* Error banner */}
        {error && (
          <div className="vc-error-banner" onClick={() => setError(null)}>
            ⚠️ {error} — tap to dismiss
          </div>
        )}

        {/* Transcript */}
        <div className="vc-transcript">
          {isConnecting && transcript.length === 0 && (
            <div className="vc-connecting-msg">
              <div className="vc-thinking-ring" />
              <p>Connecting to GPT-4o Realtime...</p>
            </div>
          )}
          {transcript.map((m, i) => (
            <div key={i} className={`vc-msg ${m.role}`}>
              {m.role === 'agent' && <div className="vc-msg-agent">🤖 GPT-4o</div>}
              <div className="vc-bubble">{m.text}</div>
              <div className="vc-msg-time">
                {m.ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </div>
            </div>
          ))}
          {liveText && (
            <div className="vc-msg customer">
              <div className="vc-bubble interim">{liveText}</div>
            </div>
          )}
          {isThinking && (
            <div className="vc-msg agent">
              <div className="vc-bubble thinking"><span /><span /><span /></div>
            </div>
          )}
          <div ref={messagesEndRef} />
        </div>

        {/* Visualiser */}
        <div className="vc-visualiser">
          {isListening && (
            <div className="vc-waves">
              <div className="vc-wave" /><div className="vc-wave" />
              <div className="vc-wave" /><div className="vc-wave" />
              <div className="vc-wave" />
            </div>
          )}
          {isSpeaking  && <div className="vc-speaking-dots"><div /><div /><div /></div>}
          {(isThinking || isConnecting) && <div className="vc-thinking-ring" />}
          {isWaiting && <div className="vc-idle-hint">Agent finished — tap mic to respond</div>}
        </div>

        {/* Latency panel */}
        <div className="vc-latency-panel">
          <button className="vc-latency-toggle" onClick={() => setShowLatency(v => !v)}>
            ⏱ Latency {showLatency ? '▲' : '▼'}
          </button>

          {showLatency && (
            <>
              {/* Current turn summary */}
              <div className="vc-latency-summary">
                <div className="vc-lat-metric">
                  <span className="vc-lat-label">VAD</span>
                  <span className="vc-lat-value" style={{ color: latencyColor(latest?.vad) }}>
                    {msLabel(latest?.vad)}
                  </span>
                  <span className="vc-lat-desc">speech duration</span>
                </div>
                <div className="vc-lat-metric">
                  <span className="vc-lat-label">TTFA</span>
                  <span className="vc-lat-value" style={{ color: latencyColor(latest?.ttfa) }}>
                    {msLabel(latest?.ttfa)}
                  </span>
                  <span className="vc-lat-desc">to first audio</span>
                </div>
                <div className="vc-lat-metric">
                  <span className="vc-lat-label">Stream</span>
                  <span className="vc-lat-value" style={{ color: latencyColor(latest?.stream) }}>
                    {msLabel(latest?.stream)}
                  </span>
                  <span className="vc-lat-desc">audio length</span>
                </div>
                <div className="vc-lat-metric">
                  <span className="vc-lat-label">Turn</span>
                  <span className="vc-lat-value" style={{ color: latencyColor(latest?.turn) }}>
                    {msLabel(latest?.turn)}
                  </span>
                  <span className="vc-lat-desc">total round trip</span>
                </div>
              </div>

              {/* History sparkline */}
              {latencyHistory.length > 1 && (
                <div className="vc-latency-history">
                  {latencyHistory.slice(0, 8).reverse().map((row, i) => (
                    <div key={i} className="vc-lat-bar-row" title={`TTFA: ${msLabel(row.ttfa)} | Turn: ${msLabel(row.turn)}`}>
                      <span className="vc-lat-bar-label">#{latencyHistory.length - i}</span>
                      <div className="vc-lat-bar-track">
                        <div
                          className="vc-lat-bar"
                          style={{
                            width: `${Math.min(100, ((row.ttfa || 0) / 2000) * 100)}%`,
                            background: latencyColor(row.ttfa),
                          }}
                        />
                      </div>
                      <span className="vc-lat-bar-val" style={{ color: latencyColor(row.ttfa) }}>
                        {msLabel(row.ttfa)}
                      </span>
                    </div>
                  ))}
                  <div className="vc-lat-legend">TTFA history (green &lt;400ms · amber &lt;800ms · red &gt;800ms)</div>
                </div>
              )}
            </>
          )}
        </div>

        {/* Controls */}
        <div className="vc-controls">
          <div className="vc-btn-row">
            <button
              className={`vc-mic-btn ${isListening ? 'active' : ''}`}
              onClick={startSpeaking}
              disabled={!isWaiting}
            >
              <span className="vc-mic-icon">
                {isListening ? '🎙️' : isSpeaking ? '🔇' : '🎤'}
              </span>
              {isListening ? 'Listening...'
                : isSpeaking ? 'Muted'
                : isThinking ? 'Thinking...'
                : isConnecting ? 'Connecting...'
                : 'Tap to Speak'}
            </button>
            <button className="vc-end-btn" onClick={endCall} disabled={callState === 'ended'}>
              <span className="vc-phone-icon">📵</span>
              End
            </button>
          </div>
          <p className="vc-hint">Powered by GPT-4o Realtime · T-Mobile LiteLLM</p>
        </div>

      </div>
    </div>
  )
}
