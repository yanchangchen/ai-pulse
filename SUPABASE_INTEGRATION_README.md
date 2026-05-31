# Supabase Integration for AI Pulse

## Overview

This document summarizes the Supabase integration changes made to the ai-pulse application.

## What Changed

### New Files

1. **`core/supabase_client.py`** (180+ lines)
   - `SupabaseManager` class for all database operations
   - Methods to save/retrieve trends, summaries, articles, and metadata
   - Graceful error handling and lazy initialization
   - Singleton pattern for efficient resource usage

2. **`core/supabase_ui.py`** (35 lines)
   - `render_supabase_status()` function for Streamlit sidebar
   - Displays cloud sync status and last run information
   - Can be imported and used in `app.py`

3. **`supabase_schema.sql`** (120+ lines)
   - Complete PostgreSQL schema for Supabase
   - 4 main tables: `trend_runs`, `theme_summaries`, `articles`, `sync_metadata`
   - Indexes for performance
   - Views for common queries
   - Row-level security (RLS) policies
   - Ready to copy-paste into Supabase SQL Editor

4. **`SUPABASE_SETUP_GUIDE.md`** (200+ lines)
   - Step-by-step setup instructions
   - How to create Supabase project
   - How to configure credentials
   - Troubleshooting guide
   - Examples of querying data

5. **`SUPABASE_INTEGRATION_README.md`** (this file)
   - Summary of changes
   - Integration architecture
   - How to use the new features

### Modified Files

1. **`core/history_manager.py`**
   - Added import for logging
   - Added `_save_to_supabase()` function
   - Modified `save_run_to_history()` to call Supabase persistence
   - Graceful degradation if Supabase is unavailable

2. **`requirements.txt`**
   - Added `supabase>=2.0.0`
   - Added `python-dotenv>=1.0.0`

3. **`.env.example`**
   - Added `SUPABASE_URL` and `SUPABASE_KEY` fields
   - Added comments explaining configuration
   - Added note about `.env` not being committed

## Architecture

### Data Flow

```
AI Pulse Run Completes
    ↓
save_run_to_history() called
    ├─→ Save to history.json ✅ (local file)
    ├─→ Save to memory.md ✅ (local file)
    └─→ _save_to_supabase() called
        ├─→ Create trend_runs record
        ├─→ Create theme_summaries records (5 themes)
        ├─→ Create articles records (all articles)
        └─→ Update sync_metadata
```

### Graceful Degradation

If Supabase is unavailable:
- App continues to work with local files
- No errors thrown
- Logged as debug/warning message
- Next run will retry Supabase connection

### Database Schema

| Table | Purpose | Key Fields |
|-------|---------|-----------|
| `trend_runs` | Each ai-pulse execution | run_timestamp, run_date, total_articles |
| `theme_summaries` | Theme insights per run | run_id, theme_name, what_is_happening, why_it_matters, what_to_watch |
| `articles` | Individual articles | run_id, theme_name, title, summary, source_name, link |
| `sync_metadata` | Sync tracking | key (last_sync_time, last_run_id, sync_status), value |

## How to Use

### 1. Setup (One-time)

```bash
# Install dependencies
pip install -r requirements.txt

# Create .env file
cp .env.example .env

# Edit .env and add Supabase credentials
# SUPABASE_URL=https://your-project.supabase.co
# SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...

# Create Supabase project and run schema
# See SUPABASE_SETUP_GUIDE.md for detailed steps
```

### 2. Run the App

```bash
streamlit run app.py
```

The app will automatically:
- Load Supabase credentials from `.env`
- Connect to Supabase (if configured)
- Save trends to both local files and Supabase after each run
- Display sync status in the sidebar

### 3. Query Your Data

**Via Supabase Dashboard:**
- Go to SQL Editor
- Write queries to analyze trends

**Via Python:**
```python
from core.supabase_client import get_supabase_manager

supabase = get_supabase_manager()
latest_run = supabase.get_latest_run()
theme_history = supabase.get_theme_history("Agentic Systems & DevTools")
```

## Integration Points

### In `history_manager.py`

The integration happens in the `save_run_to_history()` function:

```python
# After saving to local files...
_save_to_supabase(timestamp, date_key, summaries, article_counts, full_articles, themed_articles)
```

This calls `_save_to_supabase()` which:
1. Gets the Supabase manager
2. Checks if Supabase is available
3. Creates a trend_runs record
4. Saves theme summaries
5. Saves articles
6. Updates sync metadata
7. Logs the result

### In `app.py` (Optional UI)

To display Supabase status in the sidebar, add to `app.py`:

```python
from core.supabase_ui import render_supabase_status

# In the sidebar section:
render_supabase_status()
```

Or manually add the code from `core/supabase_ui.py`.

## Configuration

### Environment Variables

```env
# Required for Supabase persistence
SUPABASE_URL=https://your-project.supabase.co
SUPABASE_KEY=your-anon-key

# Existing Ollama configuration
OLLAMA_API_KEY=your-api-key
OLLAMA_MODEL=qwen3.5:cloud
```

### Disabling Supabase

Simply leave `SUPABASE_URL` and `SUPABASE_KEY` empty in `.env`. The app will work with local files only.

## Error Handling

All Supabase operations are wrapped in try-except blocks:

- **ImportError**: If `supabase` package not installed → logged as debug, app continues
- **Connection errors**: If Supabase unavailable → logged as error, app continues
- **Data errors**: If insert/update fails → logged as error, app continues

The app **never crashes** due to Supabase issues.

## Performance Considerations

- Supabase operations are synchronous (blocking)
- Typical insert time: 100-500ms for a full run
- Does not significantly impact app responsiveness
- Consider async operations if performance becomes an issue

## Security

- Uses Supabase's **anon public key** (limited permissions)
- `.env` file is in `.gitignore` (never committed)
- RLS policies included in schema (read/write for authenticated users)
- Consider additional RLS policies for production use

## Testing

To verify the integration works:

1. Set up Supabase and credentials
2. Run the app: `streamlit run app.py`
3. Check sidebar for "✅ Connected to Supabase"
4. Trigger a refresh or wait for background refresh
5. Check Supabase dashboard → Table Editor → `trend_runs`
6. Verify your latest run appears with correct data

## Troubleshooting

See `SUPABASE_SETUP_GUIDE.md` for detailed troubleshooting steps.

Common issues:
- **"Supabase not configured"** → Missing `.env` or empty credentials
- **"Connection refused"** → Check internet, verify Supabase URL
- **"No data appearing"** → Check schema was created, verify permissions

## Recent Improvements (Steps 1-4) ✅

The following enhancements have been successfully implemented:

### Step 1: Deduplication ✅
- Added unique constraint on `articles(content_hash, theme_name)` in Supabase
- Batch-level deduplication in `save_articles()`
- Uses UPSERT to prevent duplicates across runs

### Step 2: Historical Data Backfill ✅
- `backfill_from_history()` method automatically migrates existing `history.json` to Supabase
- Runs on first app startup
- Idempotent - skips runs that already exist

### Step 3: LLM Optimization ✅
- `_get_existing_article_hashes()` queries Supabase for existing articles
- `generate_all_summaries()` skips LLM calls for already-summarized articles
- Only processes new articles (better signal, faster execution)

### Step 4: Emerging Trends Visualization ✅
- New `pages/7_Emerging_Trends.py` page with 4 visualizations
- Emergence Timeline, Acceleration Index, Novelty Score, Novel Articles
- Helps identify and track emerging trends in real-time

## Future Enhancements

Possible next steps:
1. **Async persistence** - Use `asyncio` for non-blocking saves
2. **Real-time subscriptions** - Use Supabase Realtime for live updates
3. **Mobile app** - Query Supabase directly from React Native app
4. **Advanced analytics dashboard** - Complex SQL queries on trend data
5. **Data export** - Export trends to CSV/JSON for external analysis
6. **Webhooks** - Trigger actions on new trends or acceleration events

## Files Summary

| File | Lines | Purpose |
|------|-------|----------|
| `core/supabase_client.py` | 360+ | Main Supabase integration with deduplication & backfill |
| `core/supabase_ui.py` | 37 | Streamlit UI components |
| `supabase_schema.sql` | 160+ | Database schema with deduplication constraints |
| `pages/7_Emerging_Trends.py` | 362 | Emerging trends visualization page |
| `SUPABASE_SETUP_GUIDE.md` | 330+ | Setup instructions with improvements guide |
| `SUPABASE_INTEGRATION_README.md` | 280+ | This file |
| `test_supabase.py` | 68 | Integration test |
| `test_backfill.py` | 45 | Backfill test |
| `test_llm_optimization.py` | 65 | LLM optimization test |
| **Modified:** `core/history_manager.py` | +71 | Supabase persistence + backfill |
| **Modified:** `core/summariser.py` | +60 | LLM optimization |
| **Modified:** `requirements.txt` | +2 | Dependencies |
| **Modified:** `.env.example` | +9 | Configuration template |
| **Modified:** `app.py` | +3 | Backfill initialization |

## Questions?

Refer to:
- `SUPABASE_SETUP_GUIDE.md` - Step-by-step setup
- `SUPABASE_INTEGRATION_ANALYSIS.md` - Detailed design document
- `core/supabase_client.py` - Code documentation
- Supabase docs: https://supabase.com/docs
