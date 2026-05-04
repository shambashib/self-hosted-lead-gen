# Self-Hosted Lead Gen Engine

A fully self-hosted B2B/B2C lead generation engine that accepts natural language prompts and returns structured leads with contact details. No SaaS subscriptions — you own the data, the infra, and the pipeline.

---

## What It Does

Send a prompt like **"Find D2C skincare brands in Mumbai"** and the engine:

1. Parses your intent (industry, location, B2B/B2C)
2. Generates targeted search queries
3. Searches the web via **Brave Search API** (falls back to Google HTML scraping)
4. Crawls **IndiaMART**, **JustDial**, and company websites in parallel
5. Scrapes **LinkedIn company pages** (rate-limited, stealth mode)
6. Enriches each lead by visiting its listing page for real phone numbers and emails
7. Filters out irrelevant results (relevance scoring + hard negatives)
8. Deduplicates, scores (0–100), and returns structured JSON

**Output per lead:** business name · email · phone · website · city · address · industry · social links (LinkedIn, Twitter, Facebook, Instagram) · quality score · source

---

## Architecture

```
Prompt
  → PromptParser         extract industry / location / intent
  → QueryGenerator       build targeted search queries
  → BraveSearchScraper   SERP results via Brave API (or Google HTML fallback)
  → SourceClassifier     route URLs to the right crawler
  → IndiaMARTCrawler  ─┐
  → JustDialCrawler   ─┤ parallel directory crawlers
  → WebsiteCrawler    ─┘
  → LinkedInScraper      stealth company page discovery
  → ContactEnricher      visit listing pages → extract tel: links + crawl company sites
  → Normalizer           clean / standardise all fields
  → RelevanceFilter      drop off-topic leads
  → Deduplicator         fuzzy dedupe by name + phone
  → Scorer               0-100 quality score
  → Store                in-memory (default) · MongoDB · SQLite
```

---

## Requirements

- Python 3.11+
- [Brave Search API key](https://brave.com/search/api/) (free tier: 2,000 queries/month)
- Playwright Chromium (installed separately — see setup)
- Optional: Docker, MongoDB, Redis

---

## Quick Start (Local)

### 1. Clone and create a virtual environment

```bash
git clone <your-repo-url>
cd self-hosted-lead-gen

python -m venv .venv

# Windows
.venv\Scripts\activate

# macOS / Linux
source .venv/bin/activate
```

### 2. Install dependencies

```bash
pip install -r requirements.txt
```

### 3. Install Playwright browser

```bash
playwright install chromium
```

### 4. Configure environment

```bash
cp .env.example .env
```

Open `.env` and set your Brave Search API key at minimum:

```env
BRAVE_SEARCH_API_KEY=your_key_here
```

All other defaults work out of the box for local development.

### 5. Start the server

```bash
uvicorn app.main:app --reload --port 8000
```

Open `http://localhost:8000` for the web UI, or use the API directly.

---

## Docker (Recommended for Production)

Runs the API, MongoDB, and Redis together:

```bash
# Copy and fill in your env file
cp .env.example .env
# Add BRAVE_SEARCH_API_KEY=... to .env

docker compose up --build
```

API will be available at `http://localhost:8000`.

---

## API Reference

### Generate leads (async)

```http
POST /api/leads/generate
Content-Type: application/json

{
  "prompt": "Find D2C skincare brands in Mumbai"
}
```

Returns a `job_id`. Poll for status:

```http
GET /api/jobs/{job_id}
```

Fetch results when status is `completed`:

```http
GET /api/leads/{job_id}?min_score=30&limit=50
```

### Generate leads (synchronous — blocks until done)

```http
POST /api/leads/generate/sync
Content-Type: application/json

{
  "prompt": "SaaS companies in Bangalore",
  "min_score": 40
}
```

### Export to CSV

```http
GET /api/leads/export/csv?job_id={job_id}&min_score=20
```

Downloads a CSV with all lead fields.

### Interactive docs

```
http://localhost:8000/docs      ← Swagger UI
http://localhost:8000/redoc     ← ReDoc
```

---

## Configuration Reference

All settings live in `.env`. Key options:

| Variable | Default | Description |
|---|---|---|
| `BRAVE_SEARCH_API_KEY` | — | **Required.** Brave Search API key |
| `USE_PLAYWRIGHT` | `true` | Enable Chromium for JS-heavy pages (JustDial) |
| `MAX_CONCURRENT_CRAWLS` | `10` | Parallel crawler concurrency |
| `RATE_LIMIT_RPS` | `2` | Requests per second per domain |
| `SERP_RESULTS_PER_QUERY` | `10` | Results fetched per search query |
| `MAX_SERP_QUERIES` | `5` | Max queries generated per job |
| `MIN_LEAD_SCORE` | `0` | Drop leads below this score (0 = keep all) |
| `STORAGE_BACKEND` | `memory` | `memory` · `mongodb` · `sqlite` |
| `MONGODB_URI` | `mongodb://localhost:27017` | MongoDB connection string |
| `LINKEDIN_ENABLED` | `true` | Enable LinkedIn company page scraping |
| `LINKEDIN_MAX_PER_JOB` | `5` | Max LinkedIn pages per job (anti-ban cap) |
| `LLM_PROVIDER` | `none` | `none` · `openai` · `anthropic` (enhances prompt parsing) |
| `OPENAI_API_KEY` | — | Required if `LLM_PROVIDER=openai` |
| `ANTHROPIC_API_KEY` | — | Required if `LLM_PROVIDER=anthropic` |
| `PROXY_LIST` | — | Comma-separated proxy URLs for rotating requests |

---

## Lead Quality Score (0–100)

| Signal | Points |
|---|---|
| Has email | +30 |
| Business email domain (not gmail/yahoo) | +5 |
| Has phone | +20 |
| Has website | +15 |
| Has business name | +10 |
| Sourced from IndiaMART / JustDial / YellowPages | +10 |
| Has LinkedIn profile | +5 |
| Has city | +5 |
| Has address | +5 |
| Has industry tag | +5 |
| Has social links (Twitter / Facebook / Instagram) | +3 |
| No email AND no phone | −5 |

---

## Running Tests

```bash
pytest
```

---

## Project Structure

```
app/
├── api/routes/        leads.py · jobs.py
├── core/              pipeline.py · prompt_parser.py · query_generator.py
├── enrichment/        contact_enricher.py · relevance.py · scorer.py · deduplicator.py · normalizer.py
├── extractors/        contact.py · social.py
├── models/            lead.py · job.py
├── proxy/             manager.py
├── queue/             task_queue.py
├── scrapers/          serp.py · indiamart.py · justdial.py · linkedin.py · website.py · playwright_helper.py
├── storage/           database.py
├── config.py
└── main.py
frontend/              Web UI (served at /)
tests/
docker-compose.yml
Dockerfile
```

---

## Notes

- **JustDial phone numbers** are obfuscated behind JavaScript. The contact enricher visits each listing page individually and extracts `tel:` href links to get real numbers.
- **LinkedIn scraping** uses stealth Playwright with random delays (5–10s) and a hard per-job cap to avoid rate limits.
- **Windows compatibility:** Playwright runs in a `ThreadPoolExecutor` using the sync API — the only reliable approach inside uvicorn's asyncio context on Windows.
- The `--disable-http2` Chrome flag is applied to avoid `ERR_HTTP2_PROTOCOL_ERROR` on JustDial.
