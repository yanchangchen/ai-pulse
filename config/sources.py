# News sources configuration for AI Pulse

SOURCES = [
    # RSS Feeds
    {
        "name": "DeepLearning.AI The Batch",
        "url": "https://www.deeplearning.ai/the-batch/feed/",
        "type": "rss",
        "category": "newsletter"
    },
    {
        "name": "Last Week in AI",
        "url": "https://lastweekin.ai/feed",
        "type": "rss",
        "category": "newsletter"
    },
    {
        "name": "TLDR AI",
        "url": "https://tldr.tech/ai/rss",
        "type": "rss",
        "category": "newsletter"
    },
    {
        "name": "MarkTechPost",
        "url": "https://www.marktechpost.com/feed/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "MIT Technology Review AI",
        "url": "https://www.technologyreview.com/feed/",
        "type": "rss",
        "category": "news"
    },
    {
        "name": "VentureBeat AI",
        "url": "https://venturebeat.com/category/ai/feed/",
        "type": "rss",
        "category": "news"
    },
    {
        "name": "The Verge AI",
        "url": "https://www.theverge.com/rss/ai-artificial-intelligence/index.xml",
        "type": "rss",
        "category": "news"
    },
    {
        "name": "Wired AI",
        "url": "https://www.wired.com/feed/tag/ai/latest/rss",
        "type": "rss",
        "category": "news"
    },
    {
        "name": "Google AI Blog",
        "url": "https://blog.google/technology/ai/rss/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "NVIDIA AI Blog",
        "url": "https://blogs.nvidia.com/blog/category/generative-ai/feed/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "LangChain Blog",
        "url": "https://blog.langchain.dev/rss/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Neo4j Blog",
        "url": "https://neo4j.com/blog/feed/",
        "type": "rss",
        "category": "blog"
    },
    # Web sources (require scraping)
    {
        "name": "Ethan Mollick",
        "url": "https://www.oneusefulthing.org",
        "type": "web",
        "category": "blog"
    },
    {
        "name": "Lenny's Newsletter",
        "url": "https://www.lennysnewsletter.com/feed",
        "type": "rss",
        "category": "newsletter"
    },
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog",
        "type": "web",
        "category": "blog"
    },
    {
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "type": "web",
        "category": "blog"
    }
]

# Fallback web sources for when RSS fails or is unavailable
WEB_SCRAPE_SOURCES = [
    {
        "name": "Ethan Mollick",
        "url": "https://www.oneusefulthing.org",
        "selectors": {"title": "h2, h3", "summary": "p", "link": "a"}
    },
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog",
        "selectors": {"title": "h1, h2", "summary": "p", "link": "a"}
    },
    {
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "selectors": {"title": "h1, h2", "summary": "p", "link": "a"}
    }
]
