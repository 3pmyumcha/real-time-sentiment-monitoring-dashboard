# Real-Time Sentiment and Trend Monitoring Dashboard

This project implements a near-real-time data engineering pipeline for monitoring
technology discussions on Hacker News. It continuously collects posts, cleans
and stores text, detects bursty keywords, analyzes sentiment around configurable
entities, and presents the results in an auto-refreshing Streamlit dashboard.

The data source is the public Hacker News Firebase API. No API key or account is
required. The project uses a shared SQLite database as its primary data store.

## Project Overview

The pipeline follows this data flow:

```text
Hacker News API
      |
      v
Data ingestion
      |
      v
Text cleaning and normalization
      |
      v
SQLite database
      |
      +--> Trend detection
      |
      +--> Sentiment analysis
      |
      v
Streamlit dashboard
```

The system is divided into the following stages:

1. Collect recent Hacker News posts.
2. Store raw posts and cleaned posts.
3. Remove HTML, URLs, stop words, and formatting noise.
4. Detect trending terms by comparing recent frequency with a historical baseline.
5. Analyze sentiment for predefined technology entities.
6. Display trends and sentiment curves through an auto-refreshing interface.

## Database

The shared database is:

```text
data/app.db
```

It contains the following tables:

| Table | Purpose | Main fields |
|---|---|---|
| `raw_posts` | Raw ingested posts | `post_id`, `title`, `text`, `source`, `created_at`, `collected_at`, `url` |
| `clean_posts` | Cleaned analysis input | `post_id`, `title_clean`, `text_clean`, `tokens`, `created_at`, `inserted_at` |
| `trending_keywords` | Current bursty terms | `keyword`, `burst_score`, `recent_count`, `baseline_count`, `updated_at` |
| `sentiment_results` | Post-level entity sentiment | `post_id`, `entity`, positive/negative/neutral scores, `compound_score`, `sentiment_label`, `analyzed_at` |
| `sentiment_trends` | Hourly sentiment aggregates | `entity`, `time_bucket`, `post_count`, average and label counts, `sentiment_shift`, `updated_at` |

The project dashboard uses a read-only connection to `data/app.db`. Historical
trend snapshots and pipeline run records are stored separately in:

```text
data/history.db
```

Database timestamps remain in UTC. The dashboard converts timestamps to Hong
Kong Time only for display.

## Environment Setup

Using Anaconda `base`:

```powershell
cd "project"
python -m pip install -r requirements.txt
```

The required Python packages include:

- `requests`
- `vaderSentiment`
- `streamlit`
- `plotly`
- `pandas`
- `numpy`
- `pyarrow`

## Running the Project

Run the complete pipeline once:

```powershell
python run_pipeline.py --once
```

Run an offline analysis using posts already in the database:

```powershell
python run_pipeline.py --offline
```

Run a near-real-time pipeline every 60 seconds:

```powershell
python run_pipeline.py --watch --interval 60
```

Start the dashboard in a second terminal:

```powershell
python -m streamlit run dashboard.py
```

Open the displayed local address, normally:

```text
http://localhost:8501
```

For a first run or after changing the stop-word configuration, stored tokens
can be recalculated once:

```powershell
python run_pipeline.py --once --reclean
```

## Trend Detection

Trend detection compares the word frequency in the most recent hour with the
normalized frequency in the preceding 24-hour baseline. It also filters low-
frequency terms and writes the top keywords to `trending_keywords`.

The burst score can use the normalized rate formula:

```text
recent hourly rate / (baseline hourly rate + 1)
```

Set the following environment variable to restore the original ratio formula:

```powershell
$env:BURST_FORMULA="original"
```

## Sentiment Analysis

The sentiment module reads `clean_posts`, matches configured entities, and runs
VADER on the cleaned title and text.

VADER returns positive, negative, neutral, and compound scores. Compound scores
are classified as:

| Compound score | Sentiment |
|---|---|
| Greater than or equal to 0.05 | Positive |
| Less than or equal to -0.05 | Negative |
| Between -0.05 and 0.05 | Neutral |

The module writes:

- `sentiment_results`: one score per post and entity
- `sentiment_trends`: hourly aggregates for the dashboard

The hourly trend shift is marked `positive` or `negative` when the change in
average compound score is at least `0.20`; otherwise it is `stable`.

## Entity Configuration

Entity configuration is centralized in `entity_registry.py`.

The default entity profile contains:

```text
Apple
AWS
GitHub
Google
OpenAI
```

An optional slide profile contains:

```text
ChatGPT
OpenAI
Claude
Gemini
```

Switch profiles with:

```powershell
$env:ENTITY_PROFILE="slide"
python run_pipeline.py --offline
```

Entities can also be added, updated, or removed from the dashboard:

1. Expand `Manage entities` in the sidebar.
2. Enter an entity name and comma-separated aliases.
3. Click `Add or update entity`.
4. Click `Apply and re-analyze entities`.

Dashboard-managed settings are saved in:

```text
data/entity_overrides.json
```

Entity matching is case-insensitive and uses regular-expression word boundaries.
The entity name itself is always included as an alias. A clear, specific alias
list helps prevent false matches.

The current LLM-related alias set includes company names and product/model
names, for example:

```text
OpenAI: ChatGPT, GPT-4o, GPT-5, o1, o3, Sora
Anthropic: Claude, Claude 3.5, Claude Opus, Claude Haiku
Google/Gemini: Gemini, Gemini 2.5, Google Bard, Google DeepMind
Microsoft: Microsoft, Azure, Copilot, Windows
Meta: Meta AI, Llama, Llama 3, Llama 4
Mistral: Mistral, Mixtral, Codestral
xAI: Grok, Grok 2, Grok 3
NVIDIA: NVIDIA, CUDA, GeForce, RTX
```

## Dashboard

Start the dashboard with:

```powershell
python -m streamlit run dashboard.py
```

The dashboard includes:

- Overview metrics and hourly post volume
- Sentiment composition and entity curves
- Trending keyword ranking and word cloud
- Recent burst-score snapshots
- Entity detail and recent post tables
- VADER compound-score distribution
- Database and pipeline status
- Dashboard-side entity management

Auto-refresh is controlled from the sidebar. The chart area updates while sidebar
filters and the selected tab remain unchanged.

## Environment Variables

| Variable | Default | Purpose |
|---|---|---|
| `APP_DB` | `data/app.db` | Shared SQLite database path |
| `ENTITY_PROFILE` | `default` | `default` or `slide` entity profile |
| `BURST_FORMULA` | `rate` | `rate` or `original` burst formula |
| `MIN_RECENT_COUNT` | `2` | Minimum recent word frequency |
| `MIN_KEYWORDS` | `8` | Minimum keyword sample for the dashboard |

## Testing

Run module tests from the project root:

```powershell
python -m unittest discover -s tests -p "test_*.py" -v
```

Run the custom entity matching tests:

```powershell
python -m unittest tests_entity_registry.py -v
```

Compile the Python modules:

```powershell
python -m compileall -q .
```
