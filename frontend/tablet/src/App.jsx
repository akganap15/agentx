import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from './api'
import VoiceCall from './VoiceCall'
import './App.css'

const DEMO_ID = 'demo-petes-plumbing'

const SCENARIOS = [
  { id: 'inbound_lead', label: '📞 New Lead', message: 'Hi, do you fix burst pipes? My basement is flooding', color: '#00c853' },
  { id: 'after_hours', label: '🌙 After Hours', message: 'Do you have emergency service? It\'s 11pm and my water heater died', color: '#1565c0' },
  { id: 'new_review', label: '⭐ Bad Review', message: '2-star: Showed up 2 hours late and left a mess. Very disappointing.', color: '#ff8f00' },
  { id: 'booking_reminder', label: '📅 No-Show Risk', message: 'Appointment reminder for tomorrow 9am — customer hasn\'t confirmed', color: '#9c27b0' },
  { id: 'win_back', label: '💬 Win-Back', message: 'Customer Sarah Johnson hasn\'t booked in 90 days', color: '#E20074' },
]

const AGENT_ICONS = {
  lead_catcher: '🎯',
  review_pilot: '⭐',
  after_hours: '🌙',
  booking_boss: '📅',
  campaign: '📣',
  orchestrator: '🤖',
}

const AGENT_LABELS = {
  lead_catcher: 'LeadCatcher',
  review_pilot: 'ReviewPilot',
  after_hours: 'AfterHours',
  booking_boss: 'BookingBoss',
  campaign: 'Campaign',
  orchestrator: 'Orchestrator',
}

export default function App() {
  const [business, setBusiness] = useState(null)
  const [dashboard, setDashboard] = useState(null)
  const [conversations, setConversations] = useState([])
  const [activeConv, setActiveConv] = useState(null)
  const [loading, setLoading] = useState(true)
  const [simulating, setSimulating] = useState(false)
  const [activity, setActivity] = useState([])
  const [customMessage, setCustomMessage] = useState('')
  const [customScenario, setCustomScenario] = useState('inbound_lead')
  const [callActive, setCallActive] = useState(false)
  const [replyMessage, setReplyMessage] = useState('')
  const [replying, setReplying] = useState(false)
  const convEndRef = useRef(null)

  const load = useCallback(async () => {
    try {
      const [biz, dash, convs] = await Promise.all([
        api.getBusiness(DEMO_ID),
        api.getDashboard(DEMO_ID),
        api.getConversations(DEMO_ID),
      ])
      setBusiness(biz)
      setDashboard(dash)
      setConversations(Array.isArray(convs) ? convs : convs.conversations || [])
    } catch (e) {
      console.error(e)
    } finally {
      setLoading(false)
    }
  }, [])

  useEffect(() => { load() }, [load])

  useEffect(() => {
    convEndRef.current?.scrollIntoView({ behavior: 'smooth' })
  }, [activeConv])

  const runScenario = async (scenario, message) => {
    setSimulating(true)
    const msg = message || scenario.message
    const id = typeof scenario === 'string' ? scenario : scenario.id

    setActivity(prev => [{
      id: Date.now(),
      type: 'trigger',
      text: `Trigger: "${msg.slice(0, 60)}${msg.length > 60 ? '…' : ''}"`,
      ts: new Date(),
    }, ...prev.slice(0, 19)])

    try {
      const result = await api.simulate(id, msg, DEMO_ID)

      setActivity(prev => [{
        id: Date.now(),
        type: 'agent',
        agent: result.agent_used || 'orchestrator',
        text: result.agent_reply?.slice(0, 100) + (result.agent_reply?.length > 100 ? '…' : ''),
        ts: new Date(),
      }, ...prev.slice(0, 19)])

      // Add as a mock conversation
      const newConv = {
        id: result.conversation_id || `sim-${Date.now()}`,
        customer_name: 'Demo Customer',
        customer_phone: '+15550000000',
        channel: 'sms',
        agent_used: result.agent_used || 'orchestrator',
        messages: [
          { role: 'customer', content: msg, ts: new Date().toISOString() },
          { role: 'agent', content: result.agent_reply, ts: new Date().toISOString(), agent: result.agent_used },
        ],
        created_at: new Date().toISOString(),
      }
      setConversations(prev => [newConv, ...prev])
      setActiveConv(newConv)

      await load() // refresh dashboard
    } catch (e) {
      setActivity(prev => [{
        id: Date.now(),
        type: 'error',
        text: `Error: ${e.response?.data?.detail || e.message}`,
        ts: new Date(),
      }, ...prev.slice(0, 19)])
    } finally {
      setSimulating(false)
      setCustomMessage('')
    }
  }

  const sendReply = async () => {
    if (!replyMessage.trim() || !activeConv) return
    const msg = replyMessage.trim()
    setReplying(true)

    // Optimistically add customer message to thread
    const customerMsg = { role: 'customer', content: msg, ts: new Date().toISOString() }
    const updatedConv = { ...activeConv, messages: [...(activeConv.messages || []), customerMsg] }
    setActiveConv(updatedConv)
    setConversations(prev => prev.map(c => c.id === activeConv.id ? updatedConv : c))
    setReplyMessage('')

    try {
      const result = await api.simulate(activeConv.agent_used, msg, DEMO_ID, activeConv.id)
      const agentMsg = { role: 'agent', content: result.agent_reply, ts: new Date().toISOString(), agent: result.agent_used }
      const finalConv = { ...updatedConv, messages: [...updatedConv.messages, agentMsg], agent_used: result.agent_used || updatedConv.agent_used }
      setActiveConv(finalConv)
      setConversations(prev => prev.map(c => c.id === finalConv.id ? finalConv : c))
      setActivity(prev => [{
        id: Date.now(), type: 'agent',
        agent: result.agent_used || 'orchestrator',
        text: result.agent_reply?.slice(0, 100) + (result.agent_reply?.length > 100 ? '…' : ''),
        ts: new Date(),
      }, ...prev.slice(0, 19)])
    } catch (e) {
      console.error(e)
    } finally {
      setReplying(false)
    }
  }

  if (loading) return <div className="loading"><div className="spinner" /><p>Loading SMB-in-a-Box...</p></div>

  return (
    <div className="app">
      {callActive && <VoiceCall onCallEnd={() => setCallActive(false)} />}
      <Header business={business} onCall={() => setCallActive(true)} />
      <div className="layout">
        <aside className="sidebar">
          <KPIPanel dashboard={dashboard} />
          <AgentStatus />
          <ActivityFeed activity={activity} />
        </aside>
        <main className="main">
          <DemoPanel
            scenarios={SCENARIOS}
            onRun={runScenario}
            simulating={simulating}
            customMessage={customMessage}
            setCustomMessage={setCustomMessage}
            customScenario={customScenario}
            setCustomScenario={setCustomScenario}
          />
          <ConversationPanel
            conversations={conversations}
            activeConv={activeConv}
            setActiveConv={setActiveConv}
            convEndRef={convEndRef}
            replyMessage={replyMessage}
            setReplyMessage={setReplyMessage}
            onReply={sendReply}
            replying={replying}
          />
        </main>
      </div>
    </div>
  )
}

function Header({ business, onCall }) {
  return (
    <header className="header">
      <div className="header-left">
        <div className="tmo-logo">
          <span className="tmo-mark">T</span>
          <span className="tmo-text">T-Mobile</span>
        </div>
        <div className="divider-v" />
        <div className="header-biz">
          <span className="biz-name">{business?.name || 'Loading...'}</span>
          <span className="biz-industry">{business?.industry}</span>
        </div>
      </div>
      <div className="header-right">
        <button className="call-btn" onClick={onCall}>
          <span>📞</span> Simulate Call
        </button>
        <div className="status-pill active">
          <span className="pulse" />
          AI Agents Active
        </div>
        <div className="header-meta">SMB-in-a-Box Demo</div>
      </div>
    </header>
  )
}

function KPIPanel({ dashboard }) {
  const kpis = dashboard ? [
    { label: 'Leads Captured', value: dashboard.leads_captured ?? 0, icon: '🎯', color: '#00c853', bg: '#e8f9f0', delta: `${dashboard.total_conversations ?? 0} convos` },
    { label: 'Reviews Answered', value: dashboard.reviews_responded ?? 0, icon: '⭐', color: '#ff8f00', bg: '#fff8e1', delta: 'Avg 4.7★' },
    { label: 'No-Shows Prevented', value: dashboard.no_shows_recovered ?? 0, icon: '📅', color: '#1565c0', bg: '#e3f2fd', delta: `${dashboard.appointments_booked ?? 0} booked` },
    { label: 'After Hours', value: dashboard.after_hours_handled ?? 0, icon: '🌙', color: '#E20074', bg: '#f5e6ef', delta: 'This week' },
  ] : []

  return (
    <section className="kpi-panel">
      <h3 className="panel-title">This Week</h3>
      <div className="kpi-grid">
        {kpis.map(k => (
          <div className="kpi-card" key={k.label} style={{ '--accent': k.color, '--accent-bg': k.bg }}>
            <div className="kpi-icon">{k.icon}</div>
            <div className="kpi-body">
              <div className="kpi-value">{k.value}</div>
              <div className="kpi-label">{k.label}</div>
              <div className="kpi-delta">{k.delta}</div>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function AgentStatus() {
  const agents = [
    { id: 'lead_catcher', active: true },
    { id: 'review_pilot', active: true },
    { id: 'after_hours', active: true },
    { id: 'booking_boss', active: true },
    { id: 'campaign', active: false },
  ]
  return (
    <section className="agent-status">
      <h3 className="panel-title">Agent Status</h3>
      <div className="agent-list">
        {agents.map(a => (
          <div className="agent-row" key={a.id}>
            <span className="agent-icon-sm">{AGENT_ICONS[a.id]}</span>
            <span className="agent-name-sm">{AGENT_LABELS[a.id]}</span>
            <span className={`agent-badge ${a.active ? 'on' : 'off'}`}>{a.active ? 'ON' : 'OFF'}</span>
          </div>
        ))}
      </div>
    </section>
  )
}

function ActivityFeed({ activity }) {
  return (
    <section className="activity-feed">
      <h3 className="panel-title">Live Activity</h3>
      <div className="activity-list">
        {activity.length === 0 && (
          <p className="empty-state">Run a demo scenario to see live activity</p>
        )}
        {activity.map(a => (
          <div className={`activity-item ${a.type}`} key={a.id}>
            <div className="activity-dot" />
            <div className="activity-body">
              {a.agent && <span className="activity-agent">{AGENT_ICONS[a.agent]} {AGENT_LABELS[a.agent]}</span>}
              <p className="activity-text">{a.text}</p>
              <span className="activity-time">{a.ts.toLocaleTimeString()}</span>
            </div>
          </div>
        ))}
      </div>
    </section>
  )
}

function DemoPanel({ scenarios, onRun, simulating, customMessage, setCustomMessage, customScenario, setCustomScenario }) {
  return (
    <section className="demo-panel">
      <div className="demo-header">
        <h3 className="panel-title">Demo Scenarios</h3>
        <span className="demo-hint">Click to trigger an AI agent response</span>
      </div>
      <div className="scenario-grid">
        {scenarios.map(s => (
          <button
            key={s.id}
            className="scenario-btn"
            style={{ '--s-color': s.color }}
            onClick={() => onRun(s)}
            disabled={simulating}
          >
            <span className="scenario-label">{s.label}</span>
            <span className="scenario-msg">{s.message.slice(0, 55)}…</span>
          </button>
        ))}
      </div>
      <div className="custom-input-row">
        <select
          className="scenario-select"
          value={customScenario}
          onChange={e => setCustomScenario(e.target.value)}
          disabled={simulating}
        >
          {scenarios.map(s => <option key={s.id} value={s.id}>{s.label}</option>)}
        </select>
        <input
          className="custom-input"
          placeholder="Type a custom customer message..."
          value={customMessage}
          onChange={e => setCustomMessage(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && customMessage.trim() && onRun(customScenario, customMessage)}
          disabled={simulating}
        />
        <button
          className="send-btn"
          onClick={() => customMessage.trim() && onRun(customScenario, customMessage)}
          disabled={simulating || !customMessage.trim()}
        >
          {simulating ? <div className="btn-spinner" /> : '→'}
        </button>
      </div>
      {simulating && (
        <div className="thinking-bar">
          <div className="thinking-dot" /><div className="thinking-dot" /><div className="thinking-dot" />
          <span>Agent is thinking...</span>
        </div>
      )}
    </section>
  )
}

function ConversationPanel({ conversations, activeConv, setActiveConv, convEndRef, replyMessage, setReplyMessage, onReply, replying }) {
  return (
    <section className="conv-panel">
      <div className="conv-list">
        <h3 className="panel-title">Conversations</h3>
        <div className="conv-items">
          {conversations.length === 0 && <p className="empty-state">No conversations yet</p>}
          {conversations.map(c => (
            <div
              key={c.id}
              className={`conv-item ${activeConv?.id === c.id ? 'active' : ''}`}
              onClick={() => setActiveConv(c)}
            >
              <div className="conv-avatar">{c.customer_name?.[0] || '?'}</div>
              <div className="conv-info">
                <div className="conv-name">{c.customer_name || c.customer_phone}</div>
                <div className="conv-preview">
                  {c.messages?.[c.messages.length - 1]?.content?.slice(0, 45) || '...'}
                </div>
              </div>
              <div className="conv-meta">
                <span className={`agent-tag ${c.agent_used || c.agent}`}>{AGENT_ICONS[c.agent_used || c.agent]} {AGENT_LABELS[c.agent_used || c.agent]}</span>
                <span className="conv-time">{new Date(c.created_at).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
              </div>
            </div>
          ))}
        </div>
      </div>
      <div className="conv-thread">
        {!activeConv ? (
          <div className="thread-empty">
            <p>👆 Select a conversation or trigger a demo scenario</p>
          </div>
        ) : (
          <>
            <div className="thread-header">
              <div className="thread-contact">
                <div className="conv-avatar lg">{activeConv.customer_name?.[0] || '?'}</div>
                <div>
                  <div className="thread-name">{activeConv.customer_name || activeConv.customer_phone}</div>
                  <div className="thread-sub">{activeConv.customer_phone} · via {activeConv.channel}</div>
                </div>
              </div>
              <span className={`agent-tag ${activeConv.agent_used || activeConv.agent}`}>
                {AGENT_ICONS[activeConv.agent_used || activeConv.agent]} Handled by {AGENT_LABELS[activeConv.agent_used || activeConv.agent]}
              </span>
            </div>
            <div className="thread-messages">
              {(activeConv.messages || []).map((m, i) => {
                const role = m.role === 'user' ? 'customer' : m.role === 'assistant' ? 'agent' : m.role
                return (
                <div key={i} className={`message ${role}`}>
                  {role === 'agent' && (
                    <div className="msg-agent-label">
                      {AGENT_ICONS[m.agent || activeConv.agent_used]} {AGENT_LABELS[m.agent || activeConv.agent_used]}
                    </div>
                  )}
                  <div className="msg-bubble">{m.content}</div>
                  <div className="msg-time">{new Date(m.ts || m.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</div>
                </div>
                )})}
              {replying && (
                <div className="message agent">
                  <div className="msg-agent-label">{AGENT_ICONS[activeConv.agent_used]} {AGENT_LABELS[activeConv.agent_used]}</div>
                  <div className="msg-bubble typing-bubble">
                    <div className="thinking-dot" /><div className="thinking-dot" /><div className="thinking-dot" />
                  </div>
                </div>
              )}
              <div ref={convEndRef} />
            </div>
            <div className="thread-reply">
              <input
                className="reply-input"
                placeholder="Reply as customer..."
                value={replyMessage}
                onChange={e => setReplyMessage(e.target.value)}
                onKeyDown={e => e.key === 'Enter' && !replying && replyMessage.trim() && onReply()}
                disabled={replying}
              />
              <button
                className="send-btn"
                onClick={onReply}
                disabled={replying || !replyMessage.trim()}
              >
                {replying ? <div className="btn-spinner" /> : '→'}
              </button>
            </div>
          </>
        )}
      </div>
    </section>
  )
}
