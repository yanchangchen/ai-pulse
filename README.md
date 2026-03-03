# AI Pulse

An AI news intelligence dashboard that aggregates, summarises, and visualises AI developments from the past two weeks.

## Overview

AI Pulse is a multi-page Streamlit application that:
1. Fetches AI news from reputable RSS feeds and web sources
2. Categorises stories into 5 thematic areas
3. Summarises each theme using Claude (Anthropic API)
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
│  └──────────────┘    │  • Fetcher (RSS + Web scraping)      │  │
│                     │  • Classifier (Theme classification)    │  │
│                     │  • Summariser (Claude LLM summaries)   │  │
│                     │  • Visualiser (Word clouds)             │  │
│                     │  • Cache (6-hour caching)              │  │
│                     └──────────────────────────────────────┘  │
│                                                                  │
└──────────────────────────┬──────────────────────────────────────┘
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
         │              APIs                        │
         ├─────────────────────────────────────────┤
         │  • Anthropic Claude API (summaries)     │
         │  • NewsAPI (optional, extra coverage)   │
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

4. **Configure API keys:**

   Create a `.streamlit/secrets.toml` file:
   ```toml
   ANTHROPIC_API_KEY = "your-anthropic-api-key"
   NEWSAPI_KEY = "your-newsapi-key"  # optional
   ```

   Or set environment variables:
   ```bash
   export ANTHROPIC_API_KEY="your-key"
   export NEWSAPI_KEY="your-key"  # optional
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
     - `ANTHROPIC_API_KEY` = your Anthropic API key
     - `NEWSAPI_KEY` = your NewsAPI key (optional)

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
│   ├── fetcher.py          # News fetching logic (RSS + BeautifulSoup)
│   ├── classifier.py       # Theme classification (keywords + Claude)
│   ├── summariser.py       # LLM summarisation (Claude API)
│   ├── visualiser.py       # Word cloud generation
│   └── cache.py            # Caching layer (st.cache_data)
├── config/
│   ├── sources.py          # All RSS feed URLs and source metadata
│   └── themes.py           # Theme definitions and keywords
├── .streamlit/
│   └── secrets.toml.example # Example secrets configuration
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

### Claude API Usage

- **Classification**: ~$0.003 per article (keyword matching is free)
- **Summarisation**: ~$0.05-0.10 per theme (5 themes total)

**Estimated cost for a typical run:**
- 100 articles, 5 themes: ~$0.50-1.00
- 200 articles, 5 themes: ~$1.00-2.00

The app caches results for 6 hours, so you only pay once per cache period.

### NewsAPI (Optional)

- Free tier: 100 requests/day
- Paid plans: Starting at $10/month

## Troubleshooting

### "ANTHROPIC_API_KEY not configured"
Make sure you've added your API key to `.streamlit/secrets.toml` or Streamlit Cloud secrets.

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
- Summaries powered by [Anthropic Claude](https://www.anthropic.com/)
- News aggregation from various AI newsletters and blogs
