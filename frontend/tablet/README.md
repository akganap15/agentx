# SMB-in-a-Box — Tablet Dashboard (React)

This directory will contain the React tablet-optimised owner dashboard.

## Planned Tech Stack

- **Framework:** React 18 + TypeScript
- **UI Library:** Tailwind CSS + shadcn/ui
- **State Management:** Zustand
- **API Client:** TanStack Query (React Query)
- **Charts:** Recharts
- **Build Tool:** Vite

## Planned Screens

1. **Dashboard Home** — KPI cards (leads captured, appointments booked, reviews responded, avg response time)
2. **Conversations** — Live feed of all AI-handled customer interactions with transcript view
3. **Lead Pipeline** — Kanban-style view of leads by stage
4. **Business Settings** — Configure FAQs, business hours, brand voice, agent toggles
5. **Review Center** — Pending review responses, solicitation history
6. **Campaign Manager** — Win-back campaign launch, audience selection, message preview

## API Integration

All data is fetched from the backend at:
- `GET /api/v1/dashboard/{business_id}/summary`
- `GET /api/v1/dashboard/{business_id}/activity`
- `GET /api/v1/conversations/{business_id}`
- `GET /api/v1/customers/?business_id={id}`
- `POST /api/v1/events/simulate` (for live demo testing)

## Getting Started (once implemented)

```bash
cd frontend/tablet
npm install
npm run dev        # starts at http://localhost:5173
npm run build      # production build
```

## Design Notes

- Optimised for 10" tablet in landscape orientation (owner's counter device)
- Touch-friendly targets (min 44px tap targets)
- Auto-refreshes every 30 seconds for live demo effect
- Dark/light mode support
