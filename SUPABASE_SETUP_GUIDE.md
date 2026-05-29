# AI Pulse - Supabase Integration Setup Guide

This guide walks you through setting up Supabase cloud persistence for your AI Pulse trends data.

## What's New

Your ai-pulse application now supports **cloud persistence to Supabase**. This means:

✅ Trends are automatically saved to a cloud database  
✅ Access your data from multiple devices and applications  
✅ Build mobile apps that query the same trend data  
✅ Enable real-time updates with Supabase Realtime  
✅ Run SQL queries for advanced trend analytics  

**Important:** File-based persistence (local `history.json` and `memory.md`) continues to work as a fallback. Supabase is optional and gracefully degrades if unavailable.

---

## Step 1: Create a Supabase Project

1. Go to [supabase.com](https://supabase.com)
2. Sign up or log in
3. Click **"New Project"**
4. Choose a name (e.g., "ai-pulse-trends")
5. Set a strong database password
6. Select your region (closest to you)
7. Click **"Create new project"** and wait for it to initialize (~2 minutes)

---

## Step 2: Get Your Credentials

Once your project is created:

1. Go to **Project Settings** (gear icon, bottom left)
2. Click **"API"** in the left sidebar
3. Copy these values:
   - **Project URL** → `SUPABASE_URL`
   - **anon public** key → `SUPABASE_KEY`

**Example:**
```
SUPABASE_URL = https://your-project.supabase.co
SUPABASE_KEY = eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
```

---

## Step 3: Set Up the Database Schema

1. In your Supabase project, go to **SQL Editor** (left sidebar)
2. Click **"New Query"**
3. Copy the entire contents of `supabase_schema.sql` from your ai-pulse repository
4. Paste it into the SQL editor
5. Click **"Run"** (or press `Ctrl+Enter`)
6. Wait for the schema to be created (should take ~5 seconds)

**What this creates:**
- `trend_runs` table: Each ai-pulse execution
- `theme_summaries` table: Theme insights per run
- `articles` table: Individual articles with classifications
- `sync_metadata` table: Sync status and timestamps
- Indexes for performance
- Views for common queries

---

## Step 4: Configure Your Environment

1. In your ai-pulse repository root, copy `.env.example` to `.env`:
   ```bash
   cp .env.example .env
   ```

2. Edit `.env` and add your Supabase credentials:
   ```env
   SUPABASE_URL=https://your-project.supabase.co
   SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
   ```

3. **Important:** Never commit `.env` to git (it's already in `.gitignore`)

---

## Step 5: Install Dependencies

Install the new Supabase Python client:

```bash
pip install -r requirements.txt
```

This installs:
- `supabase>=2.0.0` - Supabase Python client
- `python-dotenv>=1.0.0` - Environment variable loading

---

## Step 6: Test the Integration

Run your ai-pulse app as usual:

```bash
streamlit run app.py
```

### Check the Sidebar

In the Streamlit sidebar, you should now see a new **"☁️ Cloud Sync Status"** section:

- ✅ **Connected to Supabase** - Everything is working!
- ⚠️ **Supabase not configured** - Missing credentials in `.env`
- ⚠️ **Supabase error** - Connection failed (check credentials)

### Verify Data in Supabase

After a run completes:

1. Go to your Supabase project dashboard
2. Click **"Table Editor"** (left sidebar)
3. Select `trend_runs` - you should see your latest run
4. Click on the run to expand and see related summaries and articles

---

## What Gets Persisted

Every time ai-pulse completes a run, the following is saved to Supabase:

| Data | Saved To |
|------|----------|
| Run timestamp & date | `trend_runs` |
| Theme summaries (5 themes) | `theme_summaries` |
| All articles with classifications | `articles` |
| Sync status & timestamps | `sync_metadata` |

**Example data flow:**
```
AI Pulse Run Completes
    ↓
Saves to local history.json ✅
Saves to local memory.md ✅
Saves to Supabase ✅ (if configured)
    ↓
Sidebar shows "Last sync: 2026-05-29 16:10:37"
```

---

## Querying Your Data

Once data is in Supabase, you can query it directly:

### Via Supabase Dashboard

1. Go to **SQL Editor**
2. Run queries like:

```sql
-- Get latest run
SELECT * FROM trend_runs ORDER BY run_timestamp DESC LIMIT 1;

-- Get all summaries for a theme
SELECT * FROM theme_summaries WHERE theme_name = 'Agentic Systems & DevTools' ORDER BY created_at DESC;

-- Get articles from latest run
SELECT a.* FROM articles a
JOIN trend_runs tr ON a.run_id = tr.id
WHERE tr.run_timestamp = (SELECT MAX(run_timestamp) FROM trend_runs)
ORDER BY a.theme_name;
```

### Via Python (in your code)

```python
from core.supabase_client import get_supabase_manager

supabase = get_supabase_manager()

# Get latest run
latest = supabase.get_latest_run()
print(f"Latest run: {latest['run_timestamp']}")

# Get theme history
history = supabase.get_theme_history("Frontier Models & Benchmarks", limit=5)
for summary in history:
    print(f"{summary['created_at']}: {summary['what_is_happening'][:100]}")
```

---

## Troubleshooting

### "Supabase not configured"

**Problem:** Sidebar shows "⚠️ Supabase not configured"

**Solution:**
1. Check that `.env` file exists in your ai-pulse root directory
2. Verify `SUPABASE_URL` and `SUPABASE_KEY` are set (not empty)
3. Restart the Streamlit app: `streamlit run app.py`

### "Supabase error: connection refused"

**Problem:** Sidebar shows "⚠️ Supabase error: connection refused"

**Solution:**
1. Check your internet connection
2. Verify the `SUPABASE_URL` is correct (should be `https://your-project.supabase.co`)
3. Go to Supabase dashboard and confirm your project is running

### "supabase package not installed"

**Problem:** Logs show "supabase package not installed"

**Solution:**
```bash
pip install supabase>=2.0.0 python-dotenv>=1.0.0
```

### Data not appearing in Supabase

**Problem:** App runs successfully but no data in Supabase tables

**Solution:**
1. Check the app logs for errors (look for "Failed to persist to Supabase")
2. Verify the database schema was created (check Table Editor in Supabase)
3. Confirm your `SUPABASE_KEY` has write permissions (it should by default)

---

## Disabling Supabase

If you want to disable cloud persistence temporarily:

1. Edit `.env` and comment out the Supabase variables:
   ```env
   # SUPABASE_URL=https://your-project.supabase.co
   # SUPABASE_KEY=eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9...
   ```

2. Restart the app

The app will continue to work with local file persistence only.

---

## Next Steps

Now that your data is in Supabase, you can:

1. **Build a mobile app** that displays trends (queries Supabase directly)
2. **Create a dashboard** with advanced analytics (SQL queries on trend data)
3. **Set up real-time alerts** (Supabase Realtime subscriptions)
4. **Export data** for analysis (Supabase API or SQL export)
5. **Share trends** with teammates (Supabase RLS policies)

---

## Security Notes

- Your `SUPABASE_KEY` is the **anon public key** - it has limited permissions
- Never commit `.env` to version control (already in `.gitignore`)
- For production, consider using Supabase RLS policies to restrict access
- The schema includes basic RLS policies that allow read/write access

---

## Support

- **Supabase Docs:** https://supabase.com/docs
- **AI Pulse Issues:** Check the repository issues
- **Supabase Support:** https://supabase.com/support

---

## Files Changed

New files added:
- `core/supabase_client.py` - Supabase manager class
- `core/supabase_ui.py` - Streamlit UI components
- `supabase_schema.sql` - Database schema
- `SUPABASE_SETUP_GUIDE.md` - This file

Modified files:
- `core/history_manager.py` - Added Supabase persistence
- `requirements.txt` - Added supabase and python-dotenv
- `.env.example` - Added Supabase configuration template

---

Enjoy your cloud-enabled AI Pulse! 🚀
