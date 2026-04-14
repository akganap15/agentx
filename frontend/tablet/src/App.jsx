import { useState, useEffect, useRef, useCallback } from 'react'
import { api } from './api'
import VoiceCall from './VoiceCall'
import VoiceCallRetell from './VoiceCallRetell'
import SetupWizard from './wizard/SetupWizard'
import './App.css'

const DEMO_ID = 'alex-s-plumbing'
const STORAGE_KEY = 'tchai_business_id'

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
  const [theme, setTheme] = useState('dark')
  const [loggedIn, setLoggedIn] = useState(false)
  const [needsSetup, setNeedsSetup] = useState(false)
  const [setupEmail, setSetupEmail] = useState('')
  const [businessId, setBusinessId] = useState(() => localStorage.getItem(STORAGE_KEY) || DEMO_ID)
  const [activeTab, setActiveTab] = useState('dashboard')

  useEffect(() => {
    document.documentElement.setAttribute('data-theme', theme)
  }, [theme])

  const toggleTheme = () => setTheme(t => t === 'dark' ? 'light' : 'dark')
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
  const [voiceProvider, setVoiceProvider] = useState('openai_realtime')
  const [replyMessage, setReplyMessage] = useState('')
  const [replying, setReplying] = useState(false)
  const convEndRef = useRef(null)

  const load = useCallback(async (id) => {
    const targetId = id || businessId
    setLoading(true)
    try {
      const [biz, dash, convs] = await Promise.all([
        api.getBusiness(targetId),
        api.getDashboard(targetId),
        api.getConversations(targetId),
      ])
      setBusiness(biz)
      setDashboard(dash)
      setConversations(Array.isArray(convs) ? convs : convs.conversations || [])
    } catch (e) {
      console.error('load failed for', targetId, e)
      // If stored ID fails, fall back to demo business
      if (targetId !== DEMO_ID) {
        localStorage.removeItem(STORAGE_KEY)
        setBusinessId(DEMO_ID)
        setNeedsSetup(false)
        try {
          const [biz, dash, convs] = await Promise.all([
            api.getBusiness(DEMO_ID),
            api.getDashboard(DEMO_ID),
            api.getConversations(DEMO_ID),
          ])
          setBusiness(biz)
          setDashboard(dash)
          setConversations(Array.isArray(convs) ? convs : convs.conversations || [])
        } catch (e2) {
          console.error('fallback load also failed', e2)
        }
      }
    } finally {
      setLoading(false)
    }
  }, [businessId])

  useEffect(() => { if (loggedIn) load() }, [loggedIn, load])

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
      const result = await api.simulate(id, msg, businessId)

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
      const result = await api.simulate(activeConv.agent_used, msg, businessId, activeConv.id)
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

  if (!loggedIn) return (
    <LoginScreen
      onLogin={(bizId) => {
        localStorage.setItem(STORAGE_KEY, bizId)
        setBusinessId(bizId)
        setNeedsSetup(false)
        setLoggedIn(true)
      }}
      onSetup={(email) => {
        setSetupEmail(email)
        setNeedsSetup(true)
        setLoggedIn(true)
      }}
    />
  )

  if (needsSetup) return (
    <SetupWizard
      initialEmail={setupEmail}
      onComplete={(biz) => {
        if (biz?.id) {
          setBusiness(biz)
          localStorage.setItem(STORAGE_KEY, biz.id)
          setBusinessId(biz.id)
          setNeedsSetup(false)
          load(biz.id)
        } else {
          setNeedsSetup(false)
        }
      }}
    />
  )

  if (loading) return <div className="loading"><div className="spinner" /><p>Loading SMB-in-a-Box...</p></div>

  if (!business) return (
    <div className="loading">
      <p style={{color:'var(--text-secondary)'}}>Could not load business data.</p>
      <button className="login-btn" style={{marginTop:'1rem',maxWidth:200}} onClick={() => load()}>Retry</button>
    </div>
  )

  return (
    <div className="app">
      {callActive && voiceProvider === 'openai_realtime' && <VoiceCall onCallEnd={() => setCallActive(false)} />}
      {callActive && voiceProvider === 'retell' && <VoiceCallRetell onCallEnd={() => setCallActive(false)} />}
      <Header business={business} onCall={() => setCallActive(true)} voiceProvider={voiceProvider} setVoiceProvider={setVoiceProvider} theme={theme} onToggleTheme={toggleTheme} />
      <NavBar activeTab={activeTab} onTab={setActiveTab} />

      <div className="tab-content">
        {activeTab === 'dashboard' && (
          <div className="tab-dashboard">
            <div className="dashboard-welcome">
              <div>
                <h2 className="dashboard-title">Good {getTimeOfDay()}, {business?.owner_name || 'there'} 👋</h2>
                <p className="dashboard-subtitle">Here's how your AI agents performed this week</p>
              </div>
              <div className="dashboard-date">{new Date().toLocaleDateString('en-US', { weekday: 'long', month: 'long', day: 'numeric' })}</div>
            </div>
            <KPIPanel dashboard={dashboard} />
            <DashboardActivity
              conversations={conversations}
              activity={activity}
              activeConv={activeConv}
              setActiveConv={setActiveConv}
              convEndRef={convEndRef}
            />
          </div>
        )}

        {activeTab === 'services' && (
          <ServicesTab
            business={business}
            businessId={businessId}
            onBusinessUpdate={setBusiness}
          />
        )}

        {activeTab === 'demo' && (
          <div className="tab-demo">
            <div className="tab-header">
              <h2 className="tab-title">Demo Scenarios</h2>
              <p className="tab-subtitle">Simulate customer interactions and see your AI agents respond in real time</p>
            </div>
            <div className="demo-layout">
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
            </div>
          </div>
        )}

        {activeTab === 'settings' && (
          <SettingsTab
            business={business}
            businessId={businessId}
            theme={theme}
            onToggleTheme={toggleTheme}
            onBusinessUpdate={setBusiness}
            onRelaunchWizard={() => {
              localStorage.removeItem(STORAGE_KEY)
              setNeedsSetup(true)
            }}
          />
        )}
      </div>
    </div>
  )
}

function getTimeOfDay() {
  const h = new Date().getHours()
  if (h < 12) return 'morning'
  if (h < 17) return 'afternoon'
  return 'evening'
}

function NavBar({ activeTab, onTab }) {
  const tabs = [
    { id: 'dashboard', label: 'Dashboard' },
    { id: 'services',  label: 'Manage Services' },
    { id: 'demo',      label: 'Demo' },
    { id: 'settings',  label: 'Settings' },
  ]
  return (
    <nav className="nav-bar">
      <div className="nav-tabs">
        {tabs.map(t => (
          <button
            key={t.id}
            className={`nav-tab ${activeTab === t.id ? 'active' : ''}`}
            onClick={() => onTab(t.id)}
          >
            {t.label}
          </button>
        ))}
      </div>
    </nav>
  )
}

function LoginScreen({ onLogin, onSetup }) {
  const [email, setEmail] = useState('')
  const [password, setPassword] = useState('')
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')

  const handleSubmit = async (e) => {
    e.preventDefault()
    if (!email.trim() || !password.trim()) {
      setError('Please enter your email and password.')
      return
    }
    setError('')
    setLoading(true)
    try {
      const result = await api.login(email.trim(), password)
      onLogin(result.business_id)
    } catch (err) {
      const status = err.response?.status
      if (status === 401) {
        onSetup(email.trim())
      } else {
        setError(err.response?.data?.detail || 'Sign in failed. Please try again.')
      }
    } finally {
      setLoading(false)
    }
  }

  return (
    <div className="login-screen">
      <div className="login-bg-grid" />
      <div className="login-card">
        <div className="login-logo">
          <span className="login-tmo-mark">T</span>
          <span className="login-tmo-text">T-Mobile</span>
        </div>
        <div className="login-tagline">Business AI Platform</div>
        <h1 className="login-title">Sign in to your account</h1>
        <form className="login-form" onSubmit={handleSubmit}>
          <div className="login-field">
            <label className="login-label">Business Email</label>
            <input
              className="login-input"
              type="email"
              placeholder="you@yourbusiness.com"
              value={email}
              onChange={e => setEmail(e.target.value)}
              autoFocus
              disabled={loading}
            />
          </div>
          <div className="login-field">
            <label className="login-label">Password</label>
            <input
              className="login-input"
              type="password"
              placeholder="••••••••"
              value={password}
              onChange={e => setPassword(e.target.value)}
              disabled={loading}
            />
          </div>
          {error && <p className="login-error">{error}</p>}
          <button className="login-btn" type="submit" disabled={loading}>
            {loading ? <><span className="login-spinner" /> Signing in…</> : 'Sign In'}
          </button>
        </form>
        <div className="login-footer">
          <a href="#" className="login-link">Forgot password?</a>
          <span className="login-sep">·</span>
          <a href="#" className="login-link">Create account</a>
        </div>
        <div className="login-hint">New to T-Mobile Business AI? Enter your email to get started.</div>
      </div>
      <div className="login-brand-strip">
        Powered by T-Mobile Network Intelligence &amp; Claude AI
      </div>
    </div>
  )
}

function Header({ business, onCall, voiceProvider, setVoiceProvider, theme, onToggleTheme }) {
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
        <div className="voice-toggle" title="Switch voice provider">
          <button
            className={`voice-toggle-btn ${voiceProvider === 'openai_realtime' ? 'active' : ''}`}
            onClick={() => setVoiceProvider('openai_realtime')}
          >
            OpenAI
          </button>
          <button
            className={`voice-toggle-btn ${voiceProvider === 'retell' ? 'active' : ''}`}
            onClick={() => setVoiceProvider('retell')}
          >
            Retell
          </button>
        </div>
        <button className="call-btn" onClick={onCall}>
          <span>📞</span> Simulate Call
        </button>
        <div className="status-pill active">
          <span className="pulse" />
          AI Agents Active
        </div>
        <button className="theme-toggle" onClick={onToggleTheme} title={`Switch to ${theme === 'dark' ? 'light' : 'dark'} mode`}>
          {theme === 'dark' ? '☀️' : '🌙'}
        </button>
        <div className="header-meta">SMB-in-a-Box Demo</div>
      </div>
    </header>
  )
}

function KPIPanel({ dashboard }) {
  const kpis = dashboard ? [
    {
      label: 'Leads Captured',
      value: dashboard.leads_captured ?? 0,
      icon: '🎯',
      colorVar: 'var(--green)',
      bgVar: 'var(--green-bg)',
      shadowColor: 'rgba(0,200,83,0.25)',
      delta: `+${dashboard.total_conversations ?? 0} this week`,
      trend: '↑',
      trendUp: true,
      sub: 'New inbound inquiries',
    },
    {
      label: 'Reviews Answered',
      value: dashboard.reviews_responded ?? 0,
      icon: '⭐',
      colorVar: 'var(--amber)',
      bgVar: 'var(--amber-bg)',
      shadowColor: 'rgba(255,143,0,0.25)',
      delta: 'Avg rating 4.7★',
      trend: '↑',
      trendUp: true,
      sub: 'Google reviews handled',
    },
    {
      label: 'Appointments Booked',
      value: dashboard.appointments_booked ?? 0,
      icon: '📅',
      colorVar: 'var(--blue)',
      bgVar: 'var(--blue-bg)',
      shadowColor: 'rgba(92,159,255,0.25)',
      delta: `${dashboard.no_shows_recovered ?? 0} no-shows recovered`,
      trend: '↑',
      trendUp: true,
      sub: 'Confirmed this week',
    },
    {
      label: 'After Hours Handled',
      value: dashboard.after_hours_handled ?? 0,
      icon: '🌙',
      colorVar: 'var(--magenta)',
      bgVar: 'var(--magenta-light)',
      shadowColor: 'rgba(226,0,116,0.25)',
      delta: '24/7 AI coverage',
      trend: '→',
      trendUp: null,
      sub: 'Outside business hours',
    },
  ] : []

  return (
    <section className="kpi-panel">
      <div className="kpi-grid">
        {kpis.map(k => (
          <div
            className="kpi-card"
            key={k.label}
            style={{
              '--accent': k.colorVar,
              '--accent-bg': k.bgVar,
              '--accent-shadow': k.shadowColor,
            }}
          >
            <div className="kpi-card-top">
              <div className="kpi-icon-wrap">
                <span className="kpi-icon">{k.icon}</span>
              </div>
              <div className={`kpi-trend ${k.trendUp === true ? 'up' : k.trendUp === false ? 'down' : 'flat'}`}>
                {k.trend} {k.trendUp !== null ? (k.trendUp ? 'Up' : 'Down') : 'Stable'}
              </div>
            </div>
            <div className="kpi-value">{k.value}</div>
            <div className="kpi-label">{k.label}</div>
            <div className="kpi-sub">{k.sub}</div>
            <div className="kpi-delta">{k.delta}</div>
          </div>
        ))}
      </div>
    </section>
  )
}

const AGENT_DESCRIPTIONS = {
  lead_catcher:  'Qualifies inbound leads and books appointments via SMS',
  review_pilot:  'Responds to Google reviews and solicits new ones automatically',
  after_hours:   '24/7 receptionist — answers FAQs and logs callback requests',
  booking_boss:  'Manages no-shows, waitlists, reminders and rescheduling',
  campaign:      'Runs win-back and re-engagement SMS campaigns for lapsed customers',
}

// Maps agent key → business feature flag field name
const AGENT_FLAG_MAP = {
  lead_catcher: 'lead_capture_enabled',
  review_pilot: 'review_responses_enabled',
  after_hours:  'after_hours_enabled',
  booking_boss: 'booking_enabled',
  campaign:     'campaigns_enabled',
}

function ServicesTab({ business, businessId, onBusinessUpdate }) {
  const [saving, setSaving] = useState(null) // agent id being toggled

  const handleToggle = async (agentId, enabled) => {
    const flag = AGENT_FLAG_MAP[agentId]
    if (!flag || !businessId) return
    setSaving(agentId)
    try {
      const updated = await api.updateBusiness(businessId, { [flag]: enabled })
      onBusinessUpdate(updated)
    } catch (e) {
      console.error('Failed to update agent toggle', e)
    } finally {
      setSaving(null)
    }
  }

  const activeCount = business
    ? Object.values(AGENT_FLAG_MAP).filter(f => business[f]).length
    : 0
  const pausedCount = Object.keys(AGENT_FLAG_MAP).length - activeCount

  return (
    <div className="tab-services">
      <div className="services-welcome">
        <div>
          <h2 className="dashboard-title">Manage Services</h2>
          <p className="dashboard-subtitle">
            Your AI agents — always on, always working for {business?.name || 'your business'}.
            Toggle to pause or resume any agent.
          </p>
        </div>
        <div className="services-stats">
          <span className="services-stat"><span className="services-stat-val">{activeCount}</span> Active</span>
          <span className="services-stat-sep" />
          <span className="services-stat"><span className="services-stat-val">{pausedCount}</span> Paused</span>
        </div>
      </div>
      <AgentStatus
        expanded
        business={business}
        onToggle={saving ? null : handleToggle}
      />
    </div>
  )
}

function AgentStatus({ expanded, business, onToggle }) {
  const agents = Object.keys(AGENT_FLAG_MAP).map(id => ({
    id,
    active: business ? !!business[AGENT_FLAG_MAP[id]] : id !== 'campaign',
  }))

  if (expanded) {
    return (
      <div className="agent-cards-grid">
        {agents.map(a => (
          <div className={`agent-card ${a.active ? 'active' : 'inactive'}`} key={a.id}>
            <div className="agent-card-watermark">{AGENT_ICONS[a.id]}</div>
            <div className="agent-card-top">
              <div className="agent-card-icon-wrap">{AGENT_ICONS[a.id]}</div>
              {onToggle && (
                <label className="agent-card-toggle" title={a.active ? 'Pause agent' : 'Activate agent'}>
                  <input
                    type="checkbox"
                    checked={a.active}
                    onChange={() => onToggle(a.id, !a.active)}
                  />
                  <span className="toggle-track" />
                </label>
              )}
            </div>
            <div className="agent-card-name">{AGENT_LABELS[a.id]}</div>
            <div className="agent-card-desc">{AGENT_DESCRIPTIONS[a.id]}</div>
            <div className="agent-card-footer">
              <span className={`agent-status-label ${a.active ? 'on' : 'off'}`}>
                {a.active ? '● Active' : '◌ Paused'}
              </span>
            </div>
          </div>
        ))}
      </div>
    )
  }

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

function DashboardActivity({ conversations, activity, activeConv, setActiveConv, convEndRef }) {
  // Build a customer list from conversations, deduplicated by phone
  const customers = conversations.reduce((acc, c) => {
    const phone = c.customer_phone || c.from_number || 'Unknown'
    if (!acc.find(x => x.phone === phone)) {
      acc.push({
        phone,
        name: c.customer_name || phone,
        conv: c,
        agent: c.agent_used || c.agent,
        ts: c.created_at,
      })
    }
    return acc
  }, [])

  const selected = activeConv || (customers[0]?.conv ?? null)

  // Activity items for the selected conversation
  const convActivity = activity.filter(a =>
    selected && (
      a.convId === selected.id ||
      (selected.messages || []).some(m =>
        m.content && a.text && a.text.includes(m.content.slice(0, 30))
      )
    )
  )

  return (
    <section className="dash-activity">
      <div className="dash-activity-header">
        <h3 className="panel-title">Live Activity</h3>
        {conversations.length > 0 && (
          <span className="dash-activity-count">{conversations.length} customer{conversations.length !== 1 ? 's' : ''}</span>
        )}
      </div>
      <div className="dash-activity-body">
        {/* Left — customer list */}
        <div className="dash-customer-list">
          {customers.length === 0 && (
            <p className="empty-state">No activity yet. Run a scenario in the Demo tab.</p>
          )}
          {customers.map(c => (
            <button
              key={c.phone}
              className={`dash-customer-row ${selected?.customer_phone === c.phone || selected?.from_number === c.phone ? 'active' : ''}`}
              onClick={() => setActiveConv(c.conv)}
            >
              <div className="dash-cust-avatar">{c.name?.[0]?.toUpperCase() || '#'}</div>
              <div className="dash-cust-info">
                <div className="dash-cust-phone">{c.phone}</div>
                <div className="dash-cust-preview">
                  {c.conv?.messages?.at(-1)?.content?.slice(0, 48) || '—'}
                </div>
              </div>
              <div className="dash-cust-meta">
                {c.agent && (
                  <span className={`agent-tag ${c.agent}`}>
                    {AGENT_ICONS[c.agent]}
                  </span>
                )}
                <span className="dash-cust-time">
                  {c.ts ? new Date(c.ts).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' }) : ''}
                </span>
              </div>
            </button>
          ))}
        </div>

        {/* Right — activity detail */}
        <div className="dash-activity-detail">
          {!selected ? (
            <div className="dash-detail-empty">Select a customer to view their activity</div>
          ) : (
            <>
              <div className="dash-detail-header">
                <div className="dash-detail-avatar">{selected.customer_name?.[0]?.toUpperCase() || '#'}</div>
                <div>
                  <div className="dash-detail-phone">{selected.customer_phone || selected.from_number}</div>
                  <div className="dash-detail-sub">
                    via {selected.channel || 'sms'} &nbsp;·&nbsp;
                    <span className={`agent-tag ${selected.agent_used || selected.agent}`}>
                      {AGENT_ICONS[selected.agent_used || selected.agent]} {AGENT_LABELS[selected.agent_used || selected.agent]}
                    </span>
                  </div>
                </div>
              </div>
              <div className="dash-detail-messages">
                {(selected.messages || []).map((m, i) => {
                  const role = m.role === 'user' ? 'customer' : m.role === 'assistant' ? 'agent' : m.role
                  return (
                    <div key={i} className={`message ${role}`}>
                      {role === 'agent' && (
                        <div className="msg-agent-label">
                          {AGENT_ICONS[m.agent || selected.agent_used]} {AGENT_LABELS[m.agent || selected.agent_used]}
                        </div>
                      )}
                      <div className="msg-bubble">{m.content}</div>
                      <div className="msg-time">
                        {new Date(m.ts || m.timestamp).toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}
                      </div>
                    </div>
                  )
                })}
                <div ref={convEndRef} />
              </div>
            </>
          )}
        </div>
      </div>
    </section>
  )
}

function ActivityFeed({ activity, compact }) {
  if (compact) {
    return (
      <section className="activity-feed-compact">
        <h3 className="panel-title">Live Activity</h3>
        <div className="activity-compact-list">
          {activity.length === 0 && (
            <p className="empty-state">Run a scenario in the Demo tab to see live activity here</p>
          )}
          {activity.slice(0, 6).map(a => (
            <div className={`activity-chip ${a.type}`} key={a.id}>
              {a.agent && <span>{AGENT_ICONS[a.agent]}</span>}
              <span className="activity-chip-text">{a.text}</span>
              <span className="activity-chip-time">{a.ts.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' })}</span>
            </div>
          ))}
        </div>
      </section>
    )
  }

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

function SettingsTab({ business, businessId, theme, onToggleTheme, onBusinessUpdate, onRelaunchWizard }) {
  const [bizName, setBizName] = useState(business?.name || '')
  const [industry, setIndustry] = useState(business?.industry || '')
  const [phone, setPhone] = useState(business?.phone || '')
  const [email, setEmail] = useState(business?.email || '')
  const [agents, setAgents] = useState(() => ({
    lead_catcher: business?.lead_capture_enabled ?? true,
    review_pilot: business?.review_responses_enabled ?? true,
    after_hours:  business?.after_hours_enabled ?? true,
    booking_boss: business?.booking_enabled ?? true,
    campaign:     business?.campaigns_enabled ?? false,
  }))
  const [saving, setSaving] = useState(false)
  const [saved, setSaved] = useState(false)

  const toggleAgent = (id) => setAgents(prev => ({ ...prev, [id]: !prev[id] }))

  const saveAll = async () => {
    if (!businessId) return
    setSaving(true)
    try {
      const payload = {
        name: bizName,
        industry,
        phone,
        email,
        lead_capture_enabled:      agents.lead_catcher,
        review_responses_enabled:  agents.review_pilot,
        after_hours_enabled:       agents.after_hours,
        booking_enabled:           agents.booking_boss,
        campaigns_enabled:         agents.campaign,
      }
      const updated = await api.updateBusiness(businessId, payload)
      onBusinessUpdate(updated)
      setSaved(true)
      setTimeout(() => setSaved(false), 2500)
    } catch (e) {
      console.error('Settings save failed', e)
    } finally {
      setSaving(false)
    }
  }

  return (
    <div className="tab-settings">
      <div className="settings-header">
        <h2 className="dashboard-title">Settings</h2>
        <p className="dashboard-subtitle">Manage your business profile, agents, and preferences</p>
      </div>

      <div className="settings-layout">

        {/* Business Profile */}
        <div className="settings-card">
          <div className="settings-card-header">
            <span className="settings-card-icon">🏢</span>
            <div>
              <div className="settings-card-title">Business Profile</div>
              <div className="settings-card-sub">Update how your AI agents identify your business</div>
            </div>
          </div>
          <div className="settings-fields">
            <div className="settings-row-2">
              <div className="settings-field">
                <label className="settings-label">Business Name</label>
                <input className="settings-input" value={bizName} onChange={e => setBizName(e.target.value)} placeholder="Your business name" />
              </div>
              <div className="settings-field">
                <label className="settings-label">Industry</label>
                <input className="settings-input" value={industry} onChange={e => setIndustry(e.target.value)} placeholder="e.g. Plumbing" />
              </div>
            </div>
            <div className="settings-row-2">
              <div className="settings-field">
                <label className="settings-label">Phone</label>
                <input className="settings-input" type="tel" value={phone} onChange={e => setPhone(e.target.value)} placeholder="+1 (512) 555-0100" />
              </div>
              <div className="settings-field">
                <label className="settings-label">Email</label>
                <input className="settings-input" type="email" value={email} onChange={e => setEmail(e.target.value)} placeholder="hello@yourbiz.com" />
              </div>
            </div>
          </div>
        </div>

        {/* AI Agents + Save */}
        <div className="settings-card">
          <div className="settings-card-header">
            <span className="settings-card-icon">🤖</span>
            <div>
              <div className="settings-card-title">AI Agents</div>
              <div className="settings-card-sub">Enable or pause individual agents for your business</div>
            </div>
          </div>
          <div className="settings-agents">
            {Object.entries(AGENT_META_SETTINGS).map(([id, meta]) => (
              <div key={id} className="settings-agent-row">
                <div className="settings-agent-left">
                  <span className="settings-agent-icon">{meta.icon}</span>
                  <div>
                    <div className="settings-agent-name">{meta.name}</div>
                    <div className="settings-agent-desc">{meta.desc}</div>
                  </div>
                </div>
                <button
                  className={`settings-toggle ${agents[id] ? 'on' : 'off'}`}
                  onClick={() => toggleAgent(id)}
                  type="button"
                >
                  <span className="settings-toggle-knob" />
                </button>
              </div>
            ))}
          </div>
          <div className="settings-card-footer">
            <button className="settings-btn-save" onClick={saveAll} disabled={saving}>
              {saving ? 'Saving…' : saved ? '✓ Saved' : 'Save Changes'}
            </button>
            <button className="settings-btn-ghost" onClick={onRelaunchWizard}>
              Re-run Setup Wizard
            </button>
          </div>
        </div>

        {/* Appearance */}
        <div className="settings-card">
          <div className="settings-card-header">
            <span className="settings-card-icon">🎨</span>
            <div>
              <div className="settings-card-title">Appearance</div>
              <div className="settings-card-sub">Choose your preferred color theme</div>
            </div>
          </div>
          <div className="settings-theme-row">
            <div className="settings-theme-options">
              <button
                className={`settings-theme-option ${theme === 'dark' ? 'active' : ''}`}
                onClick={() => theme !== 'dark' && onToggleTheme()}
              >
                <span className="settings-theme-preview dark-preview" />
                <span>Dark</span>
              </button>
              <button
                className={`settings-theme-option ${theme === 'light' ? 'active' : ''}`}
                onClick={() => theme !== 'light' && onToggleTheme()}
              >
                <span className="settings-theme-preview light-preview" />
                <span>Light</span>
              </button>
            </div>
          </div>
        </div>

        {/* Platform Info */}
        <div className="settings-card settings-card-info">
          <div className="settings-card-header">
            <span className="settings-card-icon">ℹ️</span>
            <div>
              <div className="settings-card-title">Platform</div>
              <div className="settings-card-sub">T-Mobile Business AI Platform</div>
            </div>
          </div>
          <div className="settings-info-rows">
            <div className="settings-info-row"><span>Model</span><span>gpt-4o-mini · LiteLLM</span></div>
            <div className="settings-info-row"><span>Agents</span><span>5 specialist agents</span></div>
            <div className="settings-info-row"><span>Version</span><span>1.0.0-beta</span></div>
            <div className="settings-info-row"><span>Status</span><span className="settings-status-pill">● Operational</span></div>
          </div>
        </div>

      </div>
    </div>
  )
}

const AGENT_META_SETTINGS = {
  lead_catcher:  { icon: '🎯', name: 'LeadCatcher',  desc: 'Qualifies inbound leads and books appointments 24/7' },
  review_pilot:  { icon: '⭐', name: 'ReviewPilot',  desc: 'Responds to Google reviews and requests new ones' },
  after_hours:   { icon: '🌙', name: 'AfterHours',   desc: '24/7 receptionist — answers FAQs and logs callbacks' },
  booking_boss:  { icon: '📅', name: 'BookingBoss',  desc: 'Manages no-shows, waitlists and rescheduling' },
  campaign:      { icon: '📣', name: 'Campaign',     desc: 'Runs win-back SMS campaigns for lapsed customers' },
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
