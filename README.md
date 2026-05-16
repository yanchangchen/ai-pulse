# AI Pulse

An AI news intelligence dashboard that aggregates, summarises, and visualises AI developments from the past two weeks.

## Overview

AI Pulse is a multi-page Streamlit application that:
1. Fetches AI news from reputable RSS feeds and web sources
2. Categorises stories into 5 thematic areas
3. Summarises each theme using Ollama Cloud (`qwen3.5:cloud`)
4. Visualises trending topics as word clouds
5. Suggests further reading per theme
6. Lists all sources used with links

## Architecture

```
┌─────────────────────────────────────────────────────────────────┐
│                        AI Pulse App                              │
├─────────────────────────────────────────────────────────────────┤
│                                                                  │
│  ┌──────────────┐    ┌──────────────┐    ┌──────────────┐      │
│  │   Overview   │    │  Deep Dive   │    │ Word Clouds  │      │
│  │    Page      │    │    Page      │    │    Page      │      │
│  └──────────────┘    └──────────────┘    └──────────────┘      │
│                                                                  │
│  ┌──────────────┐    ┌──────────────────────────────────────┐  │
│  │   Sources    │    │           Core Modules               │  │
│  │    Page      │    ├──────────────────────────────────────┤  │
│  └──────────────┘    │  • Fetcher (concurrent RSS + Web)    │  │
│                     │  • Classifier (weighted keywords +    │  │
│                     │    Ollama LLM fallback)               │  │
│                     │  • Summariser (Ollama LLM summaries)  │  │
│                     │  • Visualiser (Word clouds → PNG)     │  │
│                     │  • Cache (6-hour TTL + disk backup)   │  │
│                     │  • LLM Client (retries + backoff)     │  │
│                     └──────────────────────────────────────┘  │
│                                                                  │
│  ┌──────────────────────────────────────────────────────────┐  │
│  │               Config Layer                               │  │
│  ├──────────────────────────────────────────────────────────┤  │
│  │  • settings.py  (centralised Ollama config)              │  │
│  │  • themes.py    (weighted keyword definitions)           │  │
│  │  • sources.py   (RSS/web source registry)                │  │
│  └──────────────────────────────────────────────────────────┘  │
└──────────────────────┬──────────────────────────────────────────┘
                       │
                       ▼
         ┌─────────────────────────────────────────┐
         │           Data Sources                  │
         ├─────────────────────────────────────────┤
         │  • DeepLearning.AI The Batch            │
         │  • Last Week in AI                      │
         │  • TLDR AI                              │
         │  • MarkTechPost                        │
         │  • MIT Technology Review              │
         │  • VentureBeat AI                      │
         │  • Google AI Blog                       │
         │  • NVIDIA AI Blog                       │
         │  • LangChain Blog                       │
         │  • And more...                          │
         └─────────────────────────────────────────┘
                       │
                       ▼
         ┌─────────────────────────────────────────┐
         │          Ollama Cloud API                │
         ├─────────────────────────────────────────┤
         │  • Model: qwen3.5:cloud                 │
         │  • Classification + Summarisation       │
         │  • Exponential backoff retries           │
         └─────────────────────────────────────────┘
```

## The 5 Themes

1. **AI Applications & Architecture** - RAG, agents, LangChain, deployment, fine-tuning
2. **AI Models** - Model releases, benchmarks, GPT, Claude, Llama, multimodal
3. **AI Infrastructure** - GPU, compute, cloud, inference, latency, MLOps
4. **AI Companies & Business** - Funding, acquisitions, partnerships, valuations
5. **AI in Government & Policy** - Regulation, EU AI Act, safety, governance

## Setup Instructions

### Local Development

1. **Clone or navigate to the project:**
   ```bash
   cd ai-pulse
   ```

2. **Create a virtual environment (recommended):**
   ```bash
   python -m venv venv
   source venv/bin/activate  # On Windows: venv\Scripts\activate
   ```

3. **Install dependencies:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Configure API key:**

   Create a `.streamlit/secrets.toml` file:
   ```toml
   OLLAMA_API_KEY = "your-ollama-api-key"
   OLLAMA_MODEL = "qwen3.5:cloud"       # optional, this is the default
   OLLAMA_BASE_URL = "https://api.ollama.com"  # optional
   ```

   Or set environment variables:
   ```bash
   export OLLAMA_API_KEY="your-key"
   export OLLAMA_MODEL="qwen3.5:cloud"  # optional
   ```

5. **Run the application:**
   ```bash
   streamlit run app.py
   ```

6. **Open in browser:**
   Navigate to `http://localhost:8501`

### Streamlit Community Cloud Deployment

1. **Fork or clone this repository** to your GitHub account

2. **Add secrets in Streamlit Cloud:**
   - Go to your app settings in Streamlit Cloud
   - Add the following secrets:
     - `OLLAMA_API_KEY` = your Ollama Cloud API key
     - `OLLAMA_MODEL` = `qwen3.5:cloud` (optional)

3. **Deploy:**
   - Click "Deploy" in Streamlit Cloud
   - Select your repository
   - Set the main file as `app.py`

4. **Your app will be live at** `https://your-app-name.streamlit.app`

## Project Structure

```
ai-pulse/
├── app.py                  # Main Streamlit entry point
├── pages/
│   ├── 1_Overview.py       # Dashboard home with theme summaries
│   ├── 2_Deep_Dive.py      # Per-theme detailed view
│   ├── 3_Word_Clouds.py    # Trending topic word clouds
│   └── 4_Sources.py        # Full source list with links
├── core/
│   ├── llm_client.py       # Ollama API wrapper with retries
│   ├── fetcher.py          # Concurrent news fetching (RSS + BeautifulSoup)
│   ├── classifier.py       # Theme classification (weighted keywords + Ollama)
│   ├── summariser.py       # LLM summarisation (Ollama Cloud)
│   ├── visualiser.py       # Word cloud generation (returns PNG bytes)
│   └── cache.py            # Caching layer (st.cache_data + disk JSON)
├── config/
│   ├── settings.py         # Centralised config (secrets → env → defaults)
│   ├── sources.py          # All RSS feed URLs and source metadata
│   └── themes.py           # Theme definitions with weighted keywords
├── tests/
│   ├── test_classifier.py  # Keyword classification tests
│   ├── test_fetcher.py     # Date parsing and timezone tests
│   ├── test_summariser.py  # Further reading parser tests
│   └── test_visualiser.py  # Text preprocessing tests
├── requirements.txt
├── .env.example
└── README.md
```

## Adding New RSS Sources

To add a new news source, edit `config/sources.py`:

```python
SOURCES = [
    # Add new source:
    {
        "name": "Source Name",
        "url": "https://example.com/feed.xml",
        "type": "rss",  # or "web" for scraping
        "category": "blog"  # newsletter, news, blog
    },
    # ... existing sources
]
```

For web sources (requires BeautifulSoup scraping), add to `WEB_SCRAPE_SOURCES`.

## Cost Estimate

### Ollama Cloud API Usage

- **Classification**: Keyword matching is free; only unmatched articles hit the LLM (~$0.001 per article)
- **Summarisation**: ~$0.02–0.05 per theme (5 themes total)

**Estimated cost for a typical run:**
- 100 articles, 5 themes: ~$0.10–0.30
- 200 articles, 5 themes: ~$0.20–0.50

The app caches results for 6 hours, so you only pay once per cache period.

## Running Tests

```bash
pytest tests/ -v
```

## Troubleshooting

### "Unable to connect to Ollama Cloud"
Make sure you've added your `OLLAMA_API_KEY` to `.streamlit/secrets.toml`, environment variables, or Streamlit Cloud secrets.

### "No articles found"
- Check your internet connection
- Some RSS feeds may be temporarily unavailable
- The app filters to past 14 days - check if any articles exist in that period

### Word clouds not showing
- Ensure matplotlib and wordcloud are installed
- Some themes may have too few articles to generate a meaningful cloud

## License

MIT License

## Credits

- Built with [Streamlit](https://streamlit.io/)
- Summaries powered by [Ollama Cloud](https://ollama.com/) using `qwen3.5:cloud`
- News aggregation from various AI newsletters and blogs
