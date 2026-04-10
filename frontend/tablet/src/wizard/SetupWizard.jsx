import { useState, useEffect, useRef } from 'react'
import './SetupWizard.css'

// ─── Constants ───────────────────────────────────────────────
const STEPS = [
  { id: 'welcome',  title: 'Welcome',          icon: '👋' },
  { id: 'profile',  title: 'Business Profile', icon: '🏢' },
  { id: 'contact',  title: 'Contact',          icon: '📍' },
  { id: 'hours',    title: 'Hours',            icon: '🕐' },
  { id: 'services', title: 'Services',         icon: '⚙️' },
  { id: 'agents',   title: 'AI Agents',        icon: '🤖' },
  { id: 'review',   title: 'Review',           icon: '🚀' },
]

const INDUSTRIES = [
  'Plumbing', 'HVAC', 'Electrical', 'Landscaping / Lawn Care',
  'Auto Repair', 'Salon / Spa / Beauty', 'Restaurant / Food Service',
  'Retail', 'Medical / Dental', 'Legal Services',
  'Real Estate', 'Cleaning / Janitorial', 'Pest Control', 'Other',
]

const DAYS = ['Sunday','Monday','Tuesday','Wednesday','Thursday','Friday','Saturday']

const AGENT_META = {
  lead_catcher:  { icon: '🎯', name: 'LeadCatcher',  desc: 'Qualifies inbound leads and books appointments 24/7' },
  review_pilot:  { icon: '⭐', name: 'ReviewPilot',  desc: 'Responds to Google reviews and requests new ones automatically' },
  after_hours:   { icon: '🌙', name: 'AfterHours',   desc: '24/7 receptionist — answers FAQs and logs callback requests' },
  booking_boss:  { icon: '📅', name: 'BookingBoss',  desc: 'Manages no-shows, waitlists, reminders and rescheduling' },
  campaign:      { icon: '📣', name: 'Campaign',     desc: 'Runs win-back and re-engagement SMS campaigns for lapsed customers' },
}

const DEFAULT_DATA = {
  businessName: '', industry: '', tagline: '', description: '',
  address: '', city: '', state: '', zip: '',
  phone: '', email: '', website: '',
  hours: Object.fromEntries(DAYS.map((_, i) => [i, {
    open: i >= 1 && i <= 5,
    openTime: i === 6 ? '09:00' : '08:00',
    closeTime: i === 6 ? '14:00' : '18:00',
  }])),
  services: [{ id: crypto.randomUUID(), name: '', description: '' }],
  agents: { lead_catcher: true, review_pilot: true, after_hours: true, booking_boss: true, campaign: false },
}

// ─── Main Component ───────────────────────────────────────────
export default function SetupWizard({ onComplete, initialEmail = '' }) {
  const [step, setStep]         = useState(0)
  const [dir, setDir]           = useState('forward')
  const [data, setData]         = useState(() => ({ ...DEFAULT_DATA, email: initialEmail }))
  const [animate, setAnimate]   = useState(false)
  const [submitting, setSub]    = useState(false)
  const [error, setError]       = useState('')
  const contentRef              = useRef(null)

  const update = (patch) => setData(prev => ({ ...prev, ...patch }))

  const go = (nextStep) => {
    setDir(nextStep > step ? 'forward' : 'back')
    setAnimate(true)
    setTimeout(() => {
      setStep(nextStep)
      setAnimate(false)
      contentRef.current?.scrollTo({ top: 0, behavior: 'instant' })
    }, 220)
  }

  const handleSubmit = async () => {
    setSub(true)
    setError('')
    try {
      // Build API payload
      const slug = data.businessName.toLowerCase().replace(/[^a-z0-9]+/g, '-').replace(/^-|-$/g, '')
      const payload = {
        id: slug || `biz-${Date.now()}`,
        name: data.businessName,
        industry: data.industry,
        tagline: data.tagline,
        description: data.description,
        phone: data.phone,
        email: data.email,
        website: data.website,
        address: [data.address, data.city, data.state, data.zip].filter(Boolean).join(', '),
        hours: data.hours,
        services: data.services.filter(s => s.name.trim()),
        enabled_agents: Object.entries(data.agents).filter(([, v]) => v).map(([k]) => k),
      }
      const res = await fetch('/api/v1/businesses/', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
      })
      if (!res.ok) throw new Error(`Server error ${res.status}`)
      const biz = await res.json()
      onComplete(biz)
    } catch (e) {
      // Demo mode — proceed anyway with local data
      onComplete({ id: 'demo-new', name: data.businessName, ...data })
    } finally {
      setSub(false)
    }
  }

  const STEP_COMPONENTS = [
    <StepWelcome key="welcome" />,
    <StepProfile key="profile" data={data} update={update} />,
    <StepContact key="contact" data={data} update={update} />,
    <StepHours   key="hours"   data={data} update={update} />,
    <StepServices key="services" data={data} update={update} />,
    <StepAgents  key="agents"  data={data} update={update} />,
    <StepReview  key="review"  data={data} onEdit={go} />,
  ]

  const isFirst = step === 0
  const isLast  = step === STEPS.length - 1
  const progress = Math.round((step / (STEPS.length - 1)) * 100)

  return (
    <div className="wz-screen">
      <div className="wz-bg-grid" />

      <div className="wz-card">
        {/* Header */}
        <div className="wz-header">
          <div className="wz-logo">
            <span className="wz-tmo-mark">T</span>
            <span className="wz-tmo-text">T-Mobile</span>
          </div>
          <span className="wz-platform-label">Business AI Platform</span>
        </div>

        {/* Progress */}
        {!isFirst && (
          <div className="wz-progress-wrap">
            <div className="wz-progress-bar">
              <div className="wz-progress-fill" style={{ width: `${progress}%` }} />
            </div>
            <div className="wz-steps">
              {STEPS.slice(1).map((s, i) => {
                const stepNum = i + 1
                const done    = step > stepNum
                const active  = step === stepNum
                return (
                  <div key={s.id} className={`wz-step-pill ${done ? 'done' : ''} ${active ? 'active' : ''}`}>
                    {done ? '✓' : stepNum} <span className="wz-step-label">{s.title}</span>
                  </div>
                )
              })}
            </div>
          </div>
        )}

        {/* Step content */}
        <div
          ref={contentRef}
          className={`wz-content ${animate ? (dir === 'forward' ? 'slide-out-left' : 'slide-out-right') : ''}`}
        >
          {STEP_COMPONENTS[step]}
        </div>

        {/* Error */}
        {error && <div className="wz-error">{error}</div>}

        {/* Navigation */}
        <div className={`wz-nav ${isFirst ? 'centered' : ''}`}>
          {!isFirst && (
            <button className="wz-btn-back" onClick={() => go(step - 1)} disabled={submitting}>
              ← Back
            </button>
          )}
          {!isLast ? (
            <button className="wz-btn-next" onClick={() => go(step + 1)}>
              {isFirst ? 'Get Started →' : 'Continue →'}
            </button>
          ) : (
            <button className="wz-btn-launch" onClick={handleSubmit} disabled={submitting}>
              {submitting
                ? <><span className="wz-spinner" /> Launching…</>
                : '🚀 Launch Dashboard'}
            </button>
          )}
        </div>
      </div>

      <div className="wz-brand-strip">
        Powered by T-Mobile Network Intelligence &amp; Claude AI
      </div>
    </div>
  )
}

// ─── Step 0: Welcome ──────────────────────────────────────────
function StepWelcome() {
  return (
    <div className="wz-step">
      <div className="wz-welcome-icon">🏪</div>
      <h1 className="wz-welcome-title">Welcome to T-Mobile AI Agents</h1>
      <p className="wz-welcome-sub">
        Let's get your business set up in about 3 minutes. Your AI agents will use
        this information to handle customer calls, messages, and bookings — on your behalf.
      </p>
      <div className="wz-welcome-checklist">
        {[
          ['🏢', 'Business Profile', 'Name, industry & description'],
          ['📍', 'Contact & Hours', 'Location, phone & when you\'re open'],
          ['⚙️', 'Services & Agents', 'What you offer and which AI agents to activate'],
        ].map(([icon, title, sub]) => (
          <div className="wz-check-row" key={title}>
            <div className="wz-check-icon">{icon}</div>
            <div>
              <div className="wz-check-title">{title}</div>
              <div className="wz-check-sub">{sub}</div>
            </div>
          </div>
        ))}
      </div>
    </div>
  )
}

// ─── Step 1: Business Profile ─────────────────────────────────
function StepProfile({ data, update }) {
  return (
    <div className="wz-step">
      <h2 className="wz-step-title">Business Profile</h2>
      <p className="wz-step-sub">This helps your agents introduce your business correctly.</p>

      <div className="wz-fields">
        <div className="wz-field">
          <label className="wz-label">Business Name <span className="wz-req">*</span></label>
          <input className="wz-input" autoFocus placeholder="e.g. Andy's Plumbing"
            value={data.businessName} onChange={e => update({ businessName: e.target.value })} />
        </div>

        <div className="wz-field">
          <label className="wz-label">Industry <span className="wz-req">*</span></label>
          <select className="wz-select" value={data.industry} onChange={e => update({ industry: e.target.value })}>
            <option value="">Select your industry…</option>
            {INDUSTRIES.map(i => <option key={i} value={i}>{i}</option>)}
          </select>
        </div>

        <div className="wz-field">
          <label className="wz-label">Tagline <span className="wz-hint-label">— shown in greetings</span></label>
          <input className="wz-input" placeholder="e.g. Trusted plumbers serving Austin since 2008"
            maxLength={100} value={data.tagline} onChange={e => update({ tagline: e.target.value })} />
          <span className="wz-char-count">{data.tagline.length}/100</span>
        </div>

        <div className="wz-field">
          <label className="wz-label">Business Description <span className="wz-hint-label">— for agent context</span></label>
          <textarea className="wz-textarea" rows={4}
            placeholder="Describe what your business does, your specialties, and what makes you different…"
            maxLength={400} value={data.description} onChange={e => update({ description: e.target.value })} />
          <span className="wz-char-count">{data.description.length}/400</span>
        </div>
      </div>
    </div>
  )
}

// ─── Step 2: Contact & Location ───────────────────────────────
const US_STATES = ['AL','AK','AZ','AR','CA','CO','CT','DE','FL','GA','HI','ID','IL','IN','IA','KS','KY','LA','ME','MD','MA','MI','MN','MS','MO','MT','NE','NV','NH','NJ','NM','NY','NC','ND','OH','OK','OR','PA','RI','SC','SD','TN','TX','UT','VT','VA','WA','WV','WI','WY']

function StepContact({ data, update }) {
  return (
    <div className="wz-step">
      <h2 className="wz-step-title">Contact & Location</h2>
      <p className="wz-step-sub">Your agents will share this with customers when needed.</p>

      <div className="wz-fields">
        <div className="wz-field">
          <label className="wz-label">Street Address <span className="wz-req">*</span></label>
          <input className="wz-input" placeholder="123 Main St"
            value={data.address} onChange={e => update({ address: e.target.value })} />
        </div>

        <div className="wz-row-3">
          <div className="wz-field">
            <label className="wz-label">City <span className="wz-req">*</span></label>
            <input className="wz-input" placeholder="Austin"
              value={data.city} onChange={e => update({ city: e.target.value })} />
          </div>
          <div className="wz-field">
            <label className="wz-label">State <span className="wz-req">*</span></label>
            <select className="wz-select" value={data.state} onChange={e => update({ state: e.target.value })}>
              <option value="">—</option>
              {US_STATES.map(s => <option key={s} value={s}>{s}</option>)}
            </select>
          </div>
          <div className="wz-field">
            <label className="wz-label">ZIP <span className="wz-req">*</span></label>
            <input className="wz-input" placeholder="78701" maxLength={5}
              value={data.zip} onChange={e => update({ zip: e.target.value })} />
          </div>
        </div>

        <div className="wz-row-2">
          <div className="wz-field">
            <label className="wz-label">Business Phone <span className="wz-req">*</span></label>
            <input className="wz-input" type="tel" placeholder="+1 (512) 555-0100"
              value={data.phone} onChange={e => update({ phone: e.target.value })} />
          </div>
          <div className="wz-field">
            <label className="wz-label">Business Email <span className="wz-req">*</span></label>
            <input className="wz-input" type="email" placeholder="hello@yourbiz.com"
              value={data.email} onChange={e => update({ email: e.target.value })} />
          </div>
        </div>

        <div className="wz-field">
          <label className="wz-label">Website <span className="wz-hint-label">optional</span></label>
          <input className="wz-input" type="url" placeholder="https://yourbusiness.com"
            value={data.website} onChange={e => update({ website: e.target.value })} />
        </div>
      </div>
    </div>
  )
}

// ─── Step 3: Business Hours ───────────────────────────────────
function StepHours({ data, update }) {
  const setDay = (i, patch) => {
    update({ hours: { ...data.hours, [i]: { ...data.hours[i], ...patch } } })
  }

  const applyWeekdays = () => {
    const mon = data.hours[1]
    const next = { ...data.hours }
    for (let i = 1; i <= 5; i++) next[i] = { ...mon, open: true }
    update({ hours: next })
  }

  return (
    <div className="wz-step">
      <h2 className="wz-step-title">Business Hours</h2>
      <p className="wz-step-sub">Your agents will use these to determine after-hours handling.</p>

      <div className="wz-hours-grid">
        {DAYS.map((day, i) => {
          const h = data.hours[i]
          return (
            <div key={day} className={`wz-hour-row ${h.open ? 'open' : 'closed'}`}>
              <div className="wz-hour-day">{day.slice(0, 3)}</div>
              <button
                className={`wz-toggle ${h.open ? 'on' : 'off'}`}
                onClick={() => setDay(i, { open: !h.open })}
                type="button"
              >
                <span className="wz-toggle-knob" />
              </button>
              <span className="wz-hour-status">{h.open ? 'Open' : 'Closed'}</span>
              {h.open && (
                <div className="wz-hour-times">
                  <input type="time" className="wz-time-input" value={h.openTime}
                    onChange={e => setDay(i, { openTime: e.target.value })} />
                  <span className="wz-hour-sep">to</span>
                  <input type="time" className="wz-time-input" value={h.closeTime}
                    onChange={e => setDay(i, { closeTime: e.target.value })} />
                </div>
              )}
            </div>
          )
        })}
      </div>

      <button className="wz-btn-ghost" onClick={applyWeekdays} type="button">
        Apply Monday hours to all weekdays
      </button>
    </div>
  )
}

// ─── Step 4: Services ─────────────────────────────────────────
function StepServices({ data, update }) {
  const addService = () => {
    if (data.services.length >= 10) return
    update({ services: [...data.services, { id: crypto.randomUUID(), name: '', description: '' }] })
  }

  const removeService = (id) => {
    if (data.services.length <= 1) return
    update({ services: data.services.filter(s => s.id !== id) })
  }

  const updateService = (id, patch) => {
    update({ services: data.services.map(s => s.id === id ? { ...s, ...patch } : s) })
  }

  return (
    <div className="wz-step">
      <h2 className="wz-step-title">Services Offered</h2>
      <p className="wz-step-sub">Tell your agents what you offer so they can qualify leads accurately.</p>

      <div className="wz-services-list">
        {data.services.map((svc, idx) => (
          <div className="wz-service-card" key={svc.id}>
            <div className="wz-service-num">{idx + 1}</div>
            <div className="wz-service-fields">
              <input className="wz-input" placeholder="Service name, e.g. Drain Cleaning"
                value={svc.name} onChange={e => updateService(svc.id, { name: e.target.value })} />
              <input className="wz-input wz-input-sm" placeholder="Brief description (optional)"
                value={svc.description} onChange={e => updateService(svc.id, { description: e.target.value })} />
            </div>
            {data.services.length > 1 && (
              <button className="wz-service-remove" onClick={() => removeService(svc.id)} type="button">×</button>
            )}
          </div>
        ))}
      </div>

      {data.services.length < 10 && (
        <button className="wz-btn-ghost" onClick={addService} type="button">
          + Add another service
        </button>
      )}
    </div>
  )
}

// ─── Step 5: AI Agents ────────────────────────────────────────
function StepAgents({ data, update }) {
  const toggle = (id) => update({ agents: { ...data.agents, [id]: !data.agents[id] } })

  return (
    <div className="wz-step">
      <h2 className="wz-step-title">Choose Your AI Agents</h2>
      <p className="wz-step-sub">Select which agents to activate. You can change this anytime from the dashboard.</p>

      <div className="wz-agent-grid">
        {Object.entries(AGENT_META).map(([id, meta]) => {
          const active = data.agents[id]
          return (
            <div
              key={id}
              className={`wz-agent-card ${active ? 'active' : ''}`}
              onClick={() => toggle(id)}
            >
              <div className="wz-agent-top">
                <div className="wz-agent-icon">{meta.icon}</div>
                <div className={`wz-agent-toggle ${active ? 'on' : 'off'}`}>
                  <span className="wz-toggle-knob" />
                </div>
              </div>
              <div className="wz-agent-name">{meta.name}</div>
              <div className="wz-agent-desc">{meta.desc}</div>
            </div>
          )
        })}
      </div>
    </div>
  )
}

// ─── Step 6: Review ───────────────────────────────────────────
function StepReview({ data, onEdit }) {
  const enabledAgents = Object.entries(data.agents).filter(([, v]) => v)

  return (
    <div className="wz-step">
      <h2 className="wz-step-title">Review & Launch</h2>
      <p className="wz-step-sub">Everything looks good? Hit Launch to activate your AI agents.</p>

      <div className="wz-review-sections">

        <ReviewSection title="Business Profile" onEdit={() => onEdit(1)}>
          <ReviewRow label="Name"       value={data.businessName || '—'} />
          <ReviewRow label="Industry"   value={data.industry || '—'} />
          <ReviewRow label="Tagline"    value={data.tagline || '—'} />
        </ReviewSection>

        <ReviewSection title="Contact & Location" onEdit={() => onEdit(2)}>
          <ReviewRow label="Address"  value={[data.address, data.city, data.state, data.zip].filter(Boolean).join(', ') || '—'} />
          <ReviewRow label="Phone"    value={data.phone || '—'} />
          <ReviewRow label="Email"    value={data.email || '—'} />
          {data.website && <ReviewRow label="Website" value={data.website} />}
        </ReviewSection>

        <ReviewSection title="Business Hours" onEdit={() => onEdit(3)}>
          <div className="wz-review-hours">
            {DAYS.map((day, i) => {
              const h = data.hours[i]
              return (
                <div className="wz-review-hour-row" key={day}>
                  <span className="wz-review-day">{day.slice(0,3)}</span>
                  <span className={`wz-review-hour-val ${h.open ? '' : 'closed'}`}>
                    {h.open ? `${h.openTime} – ${h.closeTime}` : 'Closed'}
                  </span>
                </div>
              )
            })}
          </div>
        </ReviewSection>

        <ReviewSection title={`Services (${data.services.filter(s => s.name).length})`} onEdit={() => onEdit(4)}>
          <div className="wz-review-chips">
            {data.services.filter(s => s.name).map(s => (
              <span key={s.id} className="wz-chip">{s.name}</span>
            ))}
            {!data.services.some(s => s.name) && <span className="wz-review-none">No services added</span>}
          </div>
        </ReviewSection>

        <ReviewSection title={`AI Agents (${enabledAgents.length} active)`} onEdit={() => onEdit(5)}>
          <div className="wz-review-chips">
            {enabledAgents.map(([id]) => (
              <span key={id} className="wz-chip active">{AGENT_META[id].icon} {AGENT_META[id].name}</span>
            ))}
          </div>
        </ReviewSection>

      </div>
    </div>
  )
}

function ReviewSection({ title, onEdit, children }) {
  return (
    <div className="wz-review-section">
      <div className="wz-review-section-header">
        <span className="wz-review-section-title">{title}</span>
        <button className="wz-review-edit" onClick={onEdit} type="button">Edit</button>
      </div>
      <div className="wz-review-section-body">{children}</div>
    </div>
  )
}

function ReviewRow({ label, value }) {
  return (
    <div className="wz-review-row">
      <span className="wz-review-label">{label}</span>
      <span className="wz-review-value">{value}</span>
    </div>
  )
}
