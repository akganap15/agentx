# SMB-in-a-Box — Mobile App (React Native / PWA)

This directory will contain the mobile-optimised customer-facing and owner app.

## Approach: Progressive Web App (PWA)

For the hackathon we are building a PWA (installable via browser) rather than
a native app to eliminate app store submission time.

## Planned Tech Stack

- **Framework:** React 18 + TypeScript
- **UI Library:** Tailwind CSS (mobile-first) + Headless UI
- **PWA:** Vite PWA plugin (Workbox)
- **Notifications:** Web Push API
- **Build Tool:** Vite

## Planned Screens

### Owner Mobile View
1. **Notification Centre** — Push alerts for new leads, reviews, no-shows
2. **Quick Reply** — Approve or edit AI draft responses before sending
3. **Today's Appointments** — Calendar view of the day's bookings
4. **Metrics Widget** — Today's KPIs at a glance

### Customer-Facing (optional v2)
- Appointment booking flow via SMS link
- Review submission page

## API Integration

Same backend API as the tablet dashboard:
- `GET /api/v1/dashboard/{business_id}/activity`
- `POST /api/v1/events/simulate`

## Getting Started (once implemented)

```bash
cd frontend/mobile
npm install
npm run dev        # starts at http://localhost:5174
npm run build      # production PWA build
```

## Design Notes

- Designed for 375px wide mobile screens
- Bottom navigation bar pattern
- Haptic feedback for important actions (Web Vibration API)
- Offline-capable: service worker caches last-known dashboard state
- Installable: meets all PWA criteria (manifest, service worker, HTTPS)

## T-Mobile Integration

The mobile app will surface T-Mobile-specific features:
- Network quality indicator (leveraging T-Mobile SDK)
- Priority SMS delivery status for T-Mobile subscribers
- T-Mobile number management (assign/release numbers to businesses)
