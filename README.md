# Clever Process

A FastAPI service that crawls and processes web pages using
[**Camoufox**](https://camoufox.com/) — a stealth Firefox build that presents
itself as a real, human resident device and is engineered to evade bot
detectors. You send a URL to the API and get back the rendered title, cleaned
text, metadata, links, and optionally a full-page screenshot.

## Why Camoufox

Camoufox is a hardened Firefox controlled via Playwright. Unlike vanilla
Playwright/Puppeteer it:

- Generates a realistic device **fingerprint** (OS, navigator, fonts, screen,
  WebGL, headers) drawn from real-world traffic distributions.
- Derives **geolocation, timezone and locale from your outbound IP** (`geoip`)
  so they never contradict your proxy.
- **Humanizes** cursor movement so interactions look organic.
- Spoofs at the C++ level, so common JS-based bot checks don't see automation.

These are all enabled via `.env` (see below).

## Stack

- **FastAPI** + **Uvicorn** — async HTTP API
- **Camoufox (async)** — stealth headless browser, one shared instance
- **BeautifulSoup + lxml** — HTML → structured content

## Setup

```bash
cd clever-process
source venv/bin/activate
pip install -r requirements.txt

# Download the Camoufox browser binary (one-time, ~150MB)
python -m camoufox fetch

cp .env.example .env   # then edit if needed
```

> The repo already ships a `venv/`. If you recreate it, use Python 3.11+.

## Run

```bash
./run.sh
# or
uvicorn app.main:app --reload
```

Open the interactive docs at **http://localhost:8000/docs**.

For local testing the browser runs **non-headless** (`BROWSER_HEADLESS=false`)
so you can watch it work. For servers set `BROWSER_HEADLESS=virtual` (uses Xvfb
on Linux, which stays more undetectable than true headless).

## API

### `GET /health`
Liveness + whether the browser launched.

### `POST /api/v1/crawl`
Crawl and process a page.

Request body:

```json
{
  "url": "https://example.com",
  "wait_until": "domcontentloaded",
  "wait_for_selector": null,
  "wait_for_timeout_ms": 0,
  "screenshot": false,
  "extract_links": true,
  "extract_text": true,
  "return_html": true,
  "user_actions": [
    { "type": "scroll", "value": 1200 },
    { "type": "wait", "value": 1000 },
    { "type": "click", "selector": "#accept-cookies" }
  ]
}
```

Response (truncated):

```json
{
  "url": "https://example.com",
  "final_url": "https://example.com/",
  "status": 200,
  "title": "Example Domain",
  "meta": { "viewport": "width=device-width" },
  "text": "Example Domain\nThis domain is for use in ...",
  "html": "<!doctype html> ...",
  "links": [{ "text": "More information...", "href": "https://www.iana.org/domains/example" }],
  "screenshot_base64": null,
  "elapsed_ms": 1873
}
```

### Example

```bash
curl -s http://localhost:8000/api/v1/crawl \
  -H 'content-type: application/json' \
  -d '{"url":"https://example.com","screenshot":false}' | jq '.title, .elapsed_ms'
```

### `GET /api/v1/xvpn`
Runs a scripted human-like walkthrough of `https://xvpn.io/pricing`:
clicks **Get Premium** → **Credit Card**, waits 3s, returns a screenshot and a
per-step log.

### `POST /api/v1/xvpn`
Same walkthrough, then **fills the checkout form**:

- `email` from the JSON body → the "Enter the email for your X-VPN account" field
- **First / Last Name** → a random **woman's name** generated with
  [Faker](https://faker.readthedocs.io/) (`first_name_female` / `last_name_female`)
- `cardnumber`, `cardExpiry`, `cvc` from the JSON body → the Stripe card fields
  (each is a separate Stripe iframe; values are typed key-by-key so Stripe
  validates and formats them)

Request body:

```json
{
  "email": "jane.doe@example.com",
  "cardnumber": "4242424242424242",
  "cardExpiry": "12 / 28",
  "cvc": "123"
}
```

Response includes the generated name and a step log:

```json
{
  "final_url": "https://xvpn.io/pricing",
  "generated_first_name": "Brittany",
  "generated_last_name": "Morgan",
  "steps": [
    {"name": "fill_email",       "ok": true, "detail": "filled (20 chars)"},
    {"name": "fill_cardnumber",  "ok": true, "detail": "typed (16 chars)"}
  ],
  "screenshot_base64": "iVBORw0KGgo..."
}
```

> Card data is never written to logs — step details only record character counts.

```bash
curl -s -X POST http://localhost:8000/api/v1/xvpn \
  -H 'content-type: application/json' \
  -d '{"email":"jane.doe@example.com","cardnumber":"4242424242424242","cardExpiry":"12 / 28","cvc":"123"}' \
  | jq '.generated_first_name, .generated_last_name, [.steps[].name]'
```

## Configuration (`.env`)

| Var | Default | Meaning |
|-----|---------|---------|
| `BROWSER_HEADLESS` | `false` | `false` (visible) / `true` / `virtual` (Xvfb) |
| `BROWSER_HUMANIZE` | `true` | `true` or a max cursor duration in seconds |
| `BROWSER_GEOIP` | `true` | Auto geo/timezone/locale from IP, or an IP string |
| `BROWSER_OS` | `windows,macos,linux` | Fingerprint OS pool to rotate |
| `BROWSER_LOCALE` | `en-US` | Default Intl/Accept-Language locale |
| `BROWSER_BLOCK_WEBRTC` | `true` | Block WebRTC (prevents IP leaks) |
| `BROWSER_BLOCK_IMAGES` | `false` | Skip images to save bandwidth |
| `BROWSER_PROXY` | _(empty)_ | `http://user:pass@host:port` |
| `BROWSER_MAX_CONCURRENCY` | `4` | Concurrent pages on the shared browser |
| `BROWSER_NAV_TIMEOUT_MS` | `45000` | Default navigation timeout |

## How it works

```
HTTP request ──> FastAPI router ──> crawler.crawl()
                                       │
                                       ├─ acquire concurrency slot
                                       ├─ new isolated browser context
                                       ├─ page.goto() + waits + user_actions
                                       ├─ BeautifulSoup extraction
                                       └─ close context / release slot
```

One Camoufox browser is launched at app startup (FastAPI `lifespan`) and reused;
each request gets a **fresh isolated context** so cookies/state never leak
between jobs.
