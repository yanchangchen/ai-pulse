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
    {
        "name": "Anthropic Research",
        "url": "https://www.anthropic.com/research",
        "type": "web",
        "category": "blog"
    },
    {
        "name": "Anthropic Engineering",
        "url": "https://www.anthropic.com/engineering",
        "type": "web",
        "category": "blog"
    },
    {
        "name": "Meta Engineering",
        "url": "https://engineering.fb.com/category/ai/feed/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "GitHub Engineering",
        "url": "https://github.blog/category/engineering/feed/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Netflix Tech Blog",
        "url": "https://netflixtechblog.com/feed",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Spotify Engineering",
        "url": "https://engineering.atspotify.com/feed/",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Stripe Engineering",
        "url": "https://stripe.com/blog/engineering/rss",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Cloudflare Blog",
        "url": "https://blog.cloudflare.com/tag/ai/rss",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "ServiceNow Engineering",
        "url": "https://www.servicenow.com/blog.category.engineering.rss",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Scale AI Blog",
        "url": "https://scale.com/blog/rss",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Langfuse Blog",
        "url": "https://langfuse.com/blog/rss",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Latent.Space",
        "url": "https://www.latent.space/feed",
        "type": "rss",
        "category": "newsletter"
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
        "name": "Anthropic News",
        "url": "https://www.anthropic.com/news",
        "selectors": {"title": "h1, h2", "summary": "p", "link": "a"}
    }
]
