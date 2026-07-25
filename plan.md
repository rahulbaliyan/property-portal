# Property Portal — Project Plan

Real estate listing website + installable web-app for plots, flats, and villas
across Maldevta, Dehradun, Dhanaulti, and Mussoorie (Uttarakhand).

## 1. Goal

A single codebase that serves both a public website and an installable
"app" (PWA), where the owner/admin can upload and manage property listings,
and visitors anywhere in the world can browse, filter, and send inquiries.

## 2. Users

- **Visitors** (buyers/investors, worldwide): browse listings, filter by
  region/type/price, view details + photos + map, contact via form or
  WhatsApp.
- **Admin/Owner**: add/edit/remove listings, upload photos, mark sold,
  view and manage inquiries. Uses Django Admin — no custom dashboard needed
  for MVP.

## 3. Feature List

### MVP (Phase 1)
- Home page: featured/recent listings, quick search bar
- Listings page: filter by region, property type (Plot/Flat/Villa),
  price range, sort by price/date
- Property detail page: photo gallery, price, area, description, map
  location, WhatsApp "Contact" button, inquiry form
- Inquiry capture: stored in DB + visible in Django Admin (no email
  server needed for MVP — avoids extra cost/complexity)
- Admin panel: full CRUD on properties, images, inquiries (Django Admin,
  free out of the box)
- Responsive design (mobile-first — most visitors will be on phones)
- PWA: installable on Android/iOS home screen, app icon, offline shell
  for static pages

### Phase 2
- Email notifications on new inquiry (e.g. free-tier transactional email
  like Brevo/SendGrid free tier)
- Image auto-optimization/CDN (Cloudinary free tier)
- SEO: per-property meta tags, sitemap.xml, robots.txt
- Google Analytics
- Multi-agent/staff logins with permission scoping
- "Similar properties" recommendations

### Phase 3 (future)
- Native Play Store listing via Trusted Web Activity (TWA) — same
  codebase, ~$25 one-time Google Play fee
- Multi-language (Hindi/English)
- Saved/favorite listings for return visitors (requires visitor accounts)
- Payment/booking-token integration if the business model needs it

## 4. Tech Stack

| Layer | Choice | Why |
|---|---|---|
| Backend | Django 5.x | Batteries included: admin, auth, ORM, forms — fastest path for a Python developer |
| Templates | Django templates + Bootstrap 5 | Server-rendered, fast, no separate frontend build step |
| DB (dev) | SQLite | Zero setup for local development |
| DB (prod) | PostgreSQL (Supabase/Neon free tier) | Production-grade, free tier available |
| Images | Local `media/` for MVP → Cloudinary later | Avoid paid dependency until traffic justifies it |
| Maps | Leaflet.js + OpenStreetMap | Free, no API key required |
| Config | django-environ | 12-factor config via `.env`, no secrets in code |
| Static files | WhiteNoise | Serves static files in production without a separate CDN/service |
| PWA | manifest.json + service worker | Installable app experience, same code as the website |

## 5. Architecture

```
property-portal/
├── config/                 # project settings & root URLs
│   ├── settings/
│   │   ├── base.py         # shared settings
│   │   ├── dev.py          # local dev (SQLite, DEBUG=True)
│   │   └── production.py   # prod (Postgres, DEBUG=False, security headers)
│   ├── urls.py
│   ├── wsgi.py / asgi.py
├── core/                   # home page, static pages (about/contact)
├── properties/             # Property, PropertyImage models + listing views
├── inquiries/               # Inquiry model + form + admin
├── templates/               # shared templates (base.html, PWA shell)
├── static/                  # css/js/icons/manifest.json
├── media/                   # uploaded property photos (gitignored)
├── requirements.txt
├── manage.py
└── plan.md
```

Each Django app owns one responsibility (properties, inquiries, core) —
standard Django practice, keeps the codebase easy to extend without
tangled dependencies.

## 6. Data Model

**Property**
- title, slug, property_type (plot/flat/villa), region (maldevta/dehradun/
  dhanaulti/mussoorie), address, price, area_value, area_unit (sqft/nali/
  bigha/acre), bedrooms, bathrooms (nullable — plots don't have these),
  description, latitude, longitude, status (available/under_negotiation/
  sold), is_featured, created_at, updated_at

**PropertyImage**
- property (FK), image, caption, is_primary, order

**Inquiry**
- property (FK, nullable — supports general inquiries), name, phone,
  email (optional), message, created_at, is_resolved

## 7. Non-Functional Requirements

- **Performance**: paginated listing queries, `select_related`/
  `prefetch_related` for images, database indexes on region/type/status,
  compressed images.
- **Security**: CSRF protection (Django default), secrets via `.env`
  (never committed), `DEBUG=False` + `ALLOWED_HOSTS` in production,
  rate-limit the inquiry form to prevent spam, HTTPS enforced in
  production settings.
- **SEO**: descriptive URLs (slugs), meta title/description per property,
  sitemap.xml — critical since the audience is "anyone in the world"
  searching Google.
- **Accessibility**: semantic HTML, alt text on all property images.

## 8. Deployment (free tier)

| Need | Service |
|---|---|
| App hosting | Render.com free web service |
| Database | Supabase or Neon.tech (Postgres free tier) |
| Domain | Free `*.onrender.com` to start; real domain (~$8–12/yr) recommended once live for buyer trust |

Total cost to launch: **$0** (domain optional, ~$8–12/year).

## 9. Development Roadmap

1. Project scaffold, settings, git — **done**
2. Models + Django Admin (owner can start entering real listings here,
   even before public pages exist)
3. Public listing + detail pages with filters
4. Inquiry form + WhatsApp click-to-chat
5. PWA manifest/service worker
6. Deploy to Render + Postgres
7. SEO pass + Analytics
8. Phase 2 features as traffic grows

## 10. Out of scope for MVP (explicitly deferred)

- Payments/booking
- Native iOS app (Apple charges $99/year — not justified pre-traction)
- Multi-language
- Visitor accounts/favorites
