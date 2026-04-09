import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from './api'
import './VoiceCall.css'

const BUSINESS_ID = 'demo-petes-plumbing'
const GREETING = "Hi! Thanks for calling Andy Plumbing. I'm your AI assistant. How can I help you today?"
const GOODBYE_WORDS = ['goodbye', 'bye', 'hang up', "that's all", 'end call']

const AGENT_ICONS = {
  lead_catcher: '🎯', review_pilot: '⭐', after_hours: '🌙',
  booking_boss: '📅', campaign: '📣', orchestrator: '🤖',
}
const AGENT_LABELS = {
  lead_catcher: 'LeadCatcher', review_pilot: 'ReviewPilot', after_hours: 'AfterHours',
  booking_boss: 'BookingBoss', campaign: 'Campaign', orchestrator: 'Orchestrator',
}

// ── Speech helpers ───────────────────────────────────────────

function stripForSpeech(text) {
  return text
    .replace(/[^\x00-\x7F]/g, '')
    .replace(/\*+/g, '')
    .replace(/#+\s*/g, '')
    .replace(/\|[^\n]*/g, '')
    .replace(/-{3,}/g, '')
    .replace(/\d+\.\s/g, '')
    .replace(/\n+/g, ' ')
    .replace(/\s{2,}/g, ' ')
    .trim()
}

function getVoice() {
  const voices = window.speechSynthesis.getVoices()
  return voices.find(v => v.name === 'Samantha')
    || voices.find(v => v.name === 'Karen')
    || voices.find(v => v.lang === 'en-US' && v.localService)
    || voices.find(v => v.lang === 'en-US')
    || null
}

function speak(text, onDone) {
  window.speechSynthesis.cancel()
  const clean = stripForSpeech(text)
  if (!clean) { onDone?.(); return }

  const utt = new SpeechSynthesisUtterance(clean)
  utt.rate = 1.05
  utt.pitch = 1.0
  const voice = getVoice()
  if (voice) utt.voice = voice

  // Chrome bug: onend may not fire for long utterances — use timeout fallback
  const words = clean.split(' ').length
  const estimatedMs = Math.max(3000, (words / 3) * 1000)
  const fallback = setTimeout(() => { onDone?.() }, estimatedMs + 2000)

  utt.onend = () => { clearTimeout(fallback); onDone?.() }
  utt.onerror = () => { clearTimeout(fallback); onDone?.() }

  // Voices may not be loaded yet — wait if needed
  if (window.speechSynthesis.getVoices().length === 0) {
    window.speechSynthesis.onvoiceschanged = () => {
      window.speechSynthesis.onvoiceschanged = null
      const v = getVoice()
      if (v) utt.voice = v
      window.speechSynthesis.speak(utt)
    }
  } else {
    window.speechSynthesis.speak(utt)
  }
}

// ── Component ────────────────────────────────────────────────

export default function VoiceCall({ onCallEnd }) {
  const [callState, setCallState] = useState('greeting')
  const [messages, setMessages] = useState([])
  const [liveTranscript, setLiveTranscript] = useState('')
  const [agentLabel, setAgentLabel] = useState('')
  const [duration, setDuration] = useState(0)

  const recognitionRef = useRef(null)
  const conversationIdRef = useRef(null)
  const endingRef = useRef(false)
  const messagesEndRef = useRef(null)
  const timerRef = useRef(null)

  const addMessage = (role, content, agent) =>
    setMessages(prev => [...prev, { role, content, agent, ts: new Date() }])

  const endCall = useCallback(() => {
    if (endingRef.current) return
    endingRef.current = true
    setCallState('ended')
    window.speechSynthesis.cancel()
    recognitionRef.current?.abort()
    clearInterval(timerRef.current)
    speak('Thank you for calling Andy Plumbing. Have a great day! Goodbye!', () => {
      setTimeout(() => onCallEnd?.(), 600)
    })
  }, [onCallEnd])

  // Forward-declared so startListening can reference itself recursively
  const startListeningRef = useRef(null)

  const startListening = useCallback(() => {
    if (endingRef.current) return

    const SpeechRecognition = window.SpeechRecognition || window.webkitSpeechRecognition
    if (!SpeechRecognition) {
      alert('Speech recognition is not supported in this browser. Use Chrome.')
      return
    }

    const recognition = new SpeechRecognition()
    recognition.lang = 'en-US'
    recognition.interimResults = true
    recognition.maxAlternatives = 1
    recognition.continuous = false
    recognitionRef.current = recognition

    setCallState('listening')
    setLiveTranscript('')

    // ── Accumulate transcript in a local variable (not React state)
    // This avoids async state read issues in onend
    let accumulated = ''

    recognition.onresult = (e) => {
      const text = Array.from(e.results).map(r => r[0].transcript).join('')
      accumulated = text
      setLiveTranscript(text)
    }

    recognition.onend = async () => {
      if (endingRef.current) return
      const said = accumulated.trim()
      setLiveTranscript('')

      // Nothing heard — listen again
      if (!said) {
        startListeningRef.current?.()
        return
      }

      // Goodbye?
      if (GOODBYE_WORDS.some(w => said.toLowerCase().includes(w))) {
        addMessage('customer', said)
        endCall()
        return
      }

      addMessage('customer', said)
      setCallState('thinking')

      try {
        const result = await api.simulate('inbound_lead', said, BUSINESS_ID, conversationIdRef.current)
        conversationIdRef.current = result.conversation_id
        const reply = result.agent_reply || "Let me help you with that."
        const agent = result.agent_used || 'orchestrator'
        setAgentLabel(`${AGENT_ICONS[agent]} ${AGENT_LABELS[agent]}`)
        addMessage('agent', reply, agent)
        setCallState('speaking')
        speak(reply, () => {
          if (!endingRef.current) setCallState('waiting')
        })
      } catch (e) {
        console.error('Agent error:', e)
        const fallback = "I'm sorry, I had a technical issue. Could you repeat that?"
        addMessage('agent', fallback)
        setCallState('speaking')
        speak(fallback, () => {
          if (!endingRef.current) setCallState('waiting')
        })
      }
    }

    recognition.onerror = (e) => {
      console.warn('Speech recognition error:', e.error)
      if (endingRef.current) return
      if (e.error === 'no-speech' || e.error === 'audio-capture') {
        // Restart silently
        setTimeout(() => startListeningRef.current?.(), 300)
      }
    }

    recognition.start()
  }, [endCall])

  // Keep ref in sync so recursive calls work
  useEffect(() => { startListeningRef.current = startListening }, [startListening])

  // Scroll to bottom
  useEffect(() => {
    messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [messages, liveTranscript])

  // Boot: greet then listen
  useEffect(() => {
    timerRef.current = setInterval(() => setDuration(d => d + 1), 1000)

    speak(GREETING, () => {
      addMessage('agent', GREETING, 'orchestrator')
      if (!endingRef.current) setCallState('waiting')
    })

    return () => {
      clearInterval(timerRef.current)
      window.speechSynthesis.cancel()
      recognitionRef.current?.abort()
    }
  }, []) // eslint-disable-line

  const formatDuration = (s) =>
    `${String(Math.floor(s / 60)).padStart(2, '0')}:${String(s % 60).padStart(2, '0')}`

  const stateLabel = {
    greeting:  'Connecting...',
    waiting:   'Tap mic to speak',
    listening: 'Listening — speak now',
    thinking:  'Agent thinking...',
    speaking:  agentLabel || 'Speaking...',
    ended:     'Call ended',
  }[callState] || ''

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
                <span className={`vc-state-dot ${callState}`} />
                <span className="vc-state-label">{stateLabel}</span>
              </div>
            </div>
          </div>
          <div className="vc-duration">{formatDuration(duration)}</div>
        </div>

        {/* Transcript */}
        <div className="vc-transcript">
          {messages.map((m, i) => (
            <div key={i} className={`vc-msg ${m.role}`}>
              {m.role === 'agent' && m.agent && (
                <div className="vc-msg-agent">
                  {AGENT_ICONS[m.agent]} {AGENT_LABELS[m.agent]}
                </div>
              )}
              <div className="vc-bubble">{m.content}</div>
              <div className="vc-msg-time">
                {m.ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
              </div>
            </div>
          ))}

          {liveTranscript && (
            <div className="vc-msg customer">
              <div className="vc-bubble interim">{liveTranscript}</div>
            </div>
          )}

          {callState === 'thinking' && (
            <div className="vc-msg agent">
              <div className="vc-bubble thinking">
                <span /><span /><span />
              </div>
            </div>
          )}

          <div ref={messagesEndRef} />
        </div>

        {/* Visualiser */}
        <div className="vc-visualiser">
          {callState === 'listening' && (
            <div className="vc-waves">
              <div className="vc-wave" /><div className="vc-wave" />
              <div className="vc-wave" /><div className="vc-wave" />
              <div className="vc-wave" />
            </div>
          )}
          {callState === 'speaking' && (
            <div className="vc-speaking-dots">
              <div /><div /><div />
            </div>
          )}
          {callState === 'thinking' && <div className="vc-thinking-ring" />}
          {callState === 'greeting' && <div className="vc-thinking-ring" />}
          {callState === 'waiting' && <div className="vc-waiting-hint">Agent finished — tap mic to respond</div>}
        </div>

        {/* Controls */}
        <div className="vc-controls">
          <div className="vc-btn-row">
            <button
              className={`vc-mic-btn ${callState === 'listening' ? 'active' : ''}`}
              onClick={() => callState === 'waiting' && startListeningRef.current?.()}
              disabled={callState !== 'waiting' && callState !== 'listening'}
              title={callState === 'listening' ? 'Listening...' : 'Tap to speak'}
            >
              <span className="vc-mic-icon">{callState === 'listening' ? '🎙️' : '🎤'}</span>
              {callState === 'listening' ? 'Listening...' : callState === 'speaking' ? 'Muted' : 'Tap to Speak'}
            </button>
            <button className="vc-end-btn" onClick={endCall} disabled={callState === 'ended'}>
              <span className="vc-phone-icon">📵</span>
              End
            </button>
          </div>
          <p className="vc-hint">or say "Goodbye" to end</p>
        </div>

      </div>
    </div>
  )
}
