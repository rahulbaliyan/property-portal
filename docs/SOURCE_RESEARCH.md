# Uttarakhand Government Source Research

Verified source inventory for the property due-diligence agent, per the plan's
requirement: **do not invent URLs — verify them first.**

Every entry below was checked directly (fetching the live page and inspecting
its content, government emblem, and footer ownership text) rather than trusted
from search snippets alone, except where noted under "Verification method."
This document does not yet include CAPTCHA-solving flows, scraping code, or
integration design — that's a separate, later step. This is the raw map of
what exists and what it does and doesn't offer.

Last verified: 2026-08-16.

---

## 1. Registration Department (deed / sale-deed search)

| Field | Value |
|---|---|
| Department | Uttarakhand Registration Department (Stamp & Registration) |
| Official site | `https://registration.uk.gov.in/` |
| Search tool | `https://online.eregistrationukgov.in/e_search/default2.aspx` (e-Search) |
| Verification method | Fetched `registration.uk.gov.in` directly — confirmed `.gov.in` domain, Hindi department name, state emblem, footer ownership, helpline `+91 135 297 9384`. Then fetched the same page a second time asking it to list its own outbound links, to confirm the `eregistrationukgov.in` family is genuinely operated by this department (not a look-alike) — the official site directly hyperlinks to all of them. |
| Authentication | **Login required** (Username + Password) even to reach the search form. No anonymous/public search path exists on this portal. |
| CAPTCHA | Yes — present on the login page itself (image CAPTCHA), before any search. |
| Registration | A "Register Here" link exists on the login page, suggesting self-service account creation, but the signup flow itself was not tested (would require submitting real personal details). |
| Data available (per plan) | District, SRO, Khasra, Khata/Khatauni, buyer/seller name, registration number, date, property-wise transaction search — **not yet confirmed** which of these fields appear post-login, since the form is gated. |
| Key implication for the agent design | Any automation against this portal needs a **registered user account**, not just a CAPTCHA-solving step. This is a materially bigger lift than an anonymous search tool and should be flagged as a distinct integration risk/cost in the plan's Section 9–10. |

Other confirmed sibling portals under the same department (found via the
official site's own outbound links, not independently deep-verified yet):

- e-Valuation: `https://eregistrationukgov.in/e_val/Login.aspx`
- Public Data Entry: `https://eregistrationukgov.in/PDE/LOGIN.ASPX`
- GIS Ratelist: `https://gis.eregistrationukgov.in/gisportal/`
- e-Stamp: `https://portal.eregistrationukgov.in/`
- e-Payment: `https://ifms.uk.gov.in/e-chalan/elogin.aspx`

---

## 2. Revenue Department — Bhulekh (Record of Rights, Khasra/Khatauni)

| Field | Value |
|---|---|
| Department | Board of Revenue, Uttarakhand (technical support: NIC) |
| Official site | `https://bhulekh.uk.gov.in/` |
| Search tool | `https://bhulekh.uk.gov.in/public/public_ror/Public_ROR.jsp` (Public ROR) |
| Verification method | Fetched both URLs directly; confirmed state emblem, Hindi/English department name, footer "Contents Owned by Board of Revenue (Uttarakhand)" + "Technical Support By: National Informatics Center." |
| Authentication | **None required for Public ROR search** — this is genuinely open/anonymous. Login is only required for the administrative/mutation-entry side of the portal (Board/District/Tehsil admin logins, SWAMITV login), not for reading records. |
| CAPTCHA | Not observed on the Public ROR search form itself. |
| Search parameters confirmed | Cascading District → Tehsil → Village dropdowns, plus a fiscal/crop year selector, and **7 alternate search tabs**: by Khasra/Gata number, by Khata number, by owner (khatedar) name, by buyer, by seller, by mutation date, by registry. This directly matches nearly every field the plan's Section 9 asks for. |
| Data available | Record of Rights (ROR) — ownership, khasra/khata details, mutation history. |
| Other related tools on same domain | "Live Tehsil Status" (shows which tehsils have completed digitization — important caveat: some tehsils may have incomplete digitized records), SWAMITV status tracker. |
| Data limitation to flag | Digitization completeness varies by tehsil ("Live Tehsil Status" exists specifically because coverage is uneven) — the agent must check this before treating an "UNAVAILABLE" result as anything other than unavailable. |

This is the strongest, most directly usable source found so far relative to
the plan's stated needs — open access, no login, and matches most of the
required search parameters.

---

## 3. Forest Department (NOC / forest-land status)

Two distinct systems, serving different purposes — both are needed.

### 3a. State: Uttarakhand Forest Department single-window (tree felling / transit NOC)

| Field | Value |
|---|---|
| Official site | `https://forest.uk.gov.in/` |
| Applications portal | `https://www.ukfdonline.com/` (Tree Felling / Transit Permission, under the Uttarakhand Enterprises Single Window Clearance Act, 2012) |
| Verification method | Fetched both directly; `forest.uk.gov.in` footer confirms "Uttarakhand Forest Department," lists RTI/Right to Service/Citizen Charter sections, address at 85 Rajpur Road, Dehradun. `ukfdonline.com/instructions.php` footer: "Developed by Uttarakhand Forest Department." |
| Authentication | Login required (via `investuttarakhand.uk.gov.in`, credentials issued after Common Application Form submission) — this portal is for *applying*, not for anonymous status lookup. |
| Key finding for due diligence | The instructions explicitly state: if land is **recorded as "Forest" in any government record**, Forest Conservation Act 1980 provisions apply and require Government of India approval via `forestsclearance.nic.in` / PARIVESH — i.e., this state portal is not where you'd check "is this land forest," it's downstream of that determination. |
| No standalone tool found | For a plain "distance from nearest reserve forest" certificate/check — that only happens as a manual step inside the tree-felling inspection workflow, not as a public self-serve query. |

### 3b. Central: PARIVESH (Forest Clearance proposal tracking)

| Field | Value |
|---|---|
| Operator | Ministry of Environment, Forest and Climate Change, Government of India (via NIC) |
| Site | `https://parivesh.nic.in/` (proposal search: `https://cpc.parivesh.nic.in/PV_Search_Proposals.aspx`) |
| Verification method | Domain ownership (`.nic.in`, exclusively government-allocated) plus cross-confirmation from multiple independent sources (official MoEFCC references, NIC's own project page at `nic.gov.in/project/parivesh`). **Caveat**: direct WebFetch to `parivesh.nic.in` and `cpc.parivesh.nic.in` failed with a TLS certificate-chain error during this research session — the domain's authenticity is not in doubt, but reachability from an automated tool needs re-testing (may need a proper CA bundle or different fetch method; could also be a transient/regional issue). |
| Authentication | Public search/tracking of proposals does not require login; login is required only to submit or manage a proposal (per MoEFCC's own user manual). |
| Relevance | This is the actual place to check whether a specific parcel/project has an active or historical forest-clearance proposal — necessary for cross-referencing any Khasra flagged as forest-adjacent or forest-recorded. |
| Note | `forestsclearance.nic.in` (the older/legacy forest-clearance-specific domain, now largely folded into PARIVESH) also returned a TLS error on fetch — same caveat applies. |

---

## 4. Planning Authorities (Master Plan / zoning)

Coverage is split between two departments depending on location — important
because the portal's five/eight regions (Maldevta, Dehradun, Dhanaulti,
Mussoorie, Sahastradhara, Garhwal, Thano, Rishikesh) don't all fall under the
same authority.

### 4a. MDDA — Mussoorie Dehradun Development Authority

| Field | Value |
|---|---|
| Site | `https://mddaonline.in/` |
| Verification method | Fetched directly; footer "© Mussoorie Dehradun Development Authority (MDDA). All rights reserved," toll-free number, physical office in Transport Nagar, Dehradun. |
| Covers | Dehradun and Mussoorie master-plan/zonal area (this covers most of Maldevta, Dehradun, Mussoorie, and likely Sahastradhara/Thano given proximity — jurisdiction boundary not yet independently confirmed per-village). |
| Zoning info | "Zonal Plans" navigation section, "Master Plan 2041 (Draft)" document. |
| Plot-level lookup | **Not found.** No interactive tool to check the zone/land-use classification of a specific khasra number — only static plan documents and an "Auto DCR" portal for submitting building plans (not for querying existing zoning). |

### 4b. Town & Country Planning Department, Uttarakhand

| Field | Value |
|---|---|
| Site | `https://tcp.uk.gov.in/` |
| Verification method | Fetched directly; Hindi/English department name, `.gov.in` domain, footer "Content Owned by Town and Country Planning Department," hosted by NIC, last updated 2025-06-05. |
| Covers | State-level master plans, listed for Bageshwar, Chamoli, Dehradun, Nainital, Pauri, Rudraprayag, Tehri, Udham Singh Nagar, Haridwar. **Rishikesh and Mussoorie were not listed as separate master plans on the homepage** — Rishikesh likely falls under Dehradun's plan or MDDA; Dhanaulti likely falls under Tehri district's plan given it's in Tehri Garhwal. This needs a direct per-region confirmation before the agent relies on it. |
| Web GIS | Homepage references a "Web GIS Portal" with "District Portal" access, but the specific URL/capability wasn't independently reachable during this pass — needs a follow-up fetch. |

---

## 5. Court Sources (title litigation / encumbrance-adjacent risk)

### 5a. Revenue Courts (Tehsildar / SDM / DM / Commissioner / Board of Revenue)

| Field | Value |
|---|---|
| Parent site | `https://bor.uk.gov.in/` (Board of Revenue Uttarakhand — this is the live/current domain; `revenue.uk.gov.in` also appears in search results as an alternate/possibly legacy domain but failed to resolve via direct fetch during this session — DNS lookup error, needs re-check) |
| Verification method | Fetched `bor.uk.gov.in` directly — confirmed emblem, department name, CM/Chairman names shown, footer NIC-hosted, last updated 2026-03-31. |
| Case status tool | RCMS — Revenue Court Case Monitoring System, linked from the Board of Revenue site at `http://rcms.uk.gov.in/HOME/Index`. **Direct fetch failed** (connection refused) during this session — the link is genuine (found on the verified official department page, not search-engine-sourced), but live reachability is unconfirmed. A prior news report indicated RCMS was still being finalized for launch; the fact it's now linked live from the Board of Revenue homepage suggests it has since gone live, but this should be re-tested before being relied on. |
| Data need per plan | Pending mutation disputes, boundary disputes (seemankan), other revenue litigation against the subject khasra — this is the tool that would carry that data once confirmed reachable. |

### 5b. Civil Courts / High Court

| Field | Value |
|---|---|
| System | National eCourts (eCommittee, Supreme Court of India / NIC) |
| High Court (Uttarakhand) | `https://hcservices.ecourts.gov.in/ecourtindiaHC/index_highcourt.php?state_cd=15&dist_cd=1&stateNm=Uttarakhand` |
| District Court — Dehradun | `https://dehradun.dcourts.gov.in/` (case status: `.../case-status-search-by-case-number/`) |
| Verification method | Fetched the Dehradun district court case-status page directly — confirmed state emblem, "e-Courts Mission Mode Project" branding, footer "Content Owned by District Court Dehradun," hosted by NIC, logos for eCommittee Supreme Court of India and Digital India. |
| Search fields confirmed | Court complex/establishment, case type, case number, year, plus CAPTCHA (image + audio option). |
| Authentication | No login required for case-status search; CAPTCHA only. |
| Coverage gap to resolve | Other relevant district courts (Tehri for Dhanaulti, Rishikesh's own court complex if distinct from Dehradun) follow the same `<district>.dcourts.gov.in` pattern but individual URLs weren't yet verified one-by-one — low risk since the pattern is confirmed and NIC-operated, but each should still be checked before being hardcoded. |

---

## 6. GIS / Cadastral Maps (Bhu-Naksha)

| Field | Value |
|---|---|
| Site | `https://bhunaksha.uk.gov.in/` |
| Verification method | **Partial** — the domain was confirmed via the verified Board of Revenue site (`bor.uk.gov.in`), which links to it under "Ebhunaksha" in its own navigation, and cross-referenced against multiple independent secondary sources describing the same domain. However, a direct WebFetch to `bhunaksha.uk.gov.in` failed (`ECONNREFUSED`) during this session, so its live content/form fields were **not directly inspected** — this entry is one step short of the verification bar the rest of this document meets and should be re-fetched before being relied on. |
| Expected capability (per secondary sources, not yet independently confirmed) | District → Tehsil → Village selection, then click-to-view cadastral map with khasra boundaries; explicit disclaimer that these maps are for verification/planning, not legal proof of ownership. |
| Relation to Bhulekh | Same underlying Revenue Department / NIC system as the Public ROR tool in Section 2 — this is the spatial/map counterpart to that textual record. |

---

## Summary: what's solid vs. what needs another pass

**Solid (directly fetched, content verified, matches plan requirements):**
- Bhulekh Public ROR (Section 2) — best match to the plan's needs; open access.
- Registration Department identity (Section 1) — confirmed genuine, but gated behind login+CAPTCHA, which changes the integration design.
- Forest Department dual-system structure (Section 3) — state NOC portal vs. central PARIVESH clearance tracker, and the important rule that "recorded as Forest" triggers central not state jurisdiction.
- MDDA and TCP identity and general scope (Section 4) — confirmed genuine, but neither exposes a plot-level zoning lookup tool.
- Dehradun district court + High Court eCourts (Section 5b) — confirmed genuine, fields enumerated.

**Needs a follow-up verification pass before the agent design relies on it:**
- `bhunaksha.uk.gov.in` — connection refused, not yet directly inspected (Section 6).
- `rcms.uk.gov.in` — connection refused; genuine link but live status unconfirmed (Section 5a).
- `parivesh.nic.in` / `cpc.parivesh.nic.in` / `forestsclearance.nic.in` — TLS certificate error on fetch; domain legitimacy is solid, reachability from tooling is not (Section 3b).
- `revenue.uk.gov.in` — DNS resolution failure; `bor.uk.gov.in` works and appears to be the current canonical domain, but this should be reconciled (Section 5a).
- Which district's master plan/court actually governs Rishikesh, Dhanaulti, Sahastradhara, Garhwal, Thano specifically — jurisdiction boundaries were inferred from geography, not confirmed per-village (Section 4b, 5b).

None of the connection failures above are evidence a source is fake — they're
logged here so the next pass re-tests them (possibly needing a different
network path, since several Indian government sites are flaky or
geo/IP-sensitive) rather than either trusting or discarding them on faith.
