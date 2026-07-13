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
        "name": "Latent.Space",
        "url": "https://www.latent.space/feed",
        "type": "rss",
        "category": "newsletter"
    },
    # AI Labs (Tier 1 — Appendix C1)
    {
        "name": "OpenAI Blog",
        "url": "https://openai.com/blog/rss.xml",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Google DeepMind Blog",
        "url": "https://deepmind.google/blog/rss.xml",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Hugging Face Blog",
        "url": "https://huggingface.co/blog/feed.xml",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Meta AI Blog",
        "url": "https://ai.meta.com/blog/rss/",
        "type": "rss",
        "category": "blog"
    },
    # Agent & Framework Engineering (Tier 1 — Appendix C2)
    {
        "name": "Weights & Biases Fully Connected",
        "url": "https://wandb.ai/fully-connected/rss.xml",
        "type": "rss",
        "category": "blog"
    },
    # Big Tech Cloud (Tier 1 — Appendix C3)
    {
        "name": "AWS Machine Learning Blog",
        "url": "https://aws.amazon.com/blogs/machine-learning/feed/",
        "type": "rss",
        "category": "blog"
    },
    # Inference & Hardware (Tier 2 — Appendix C5)
    {
        "name": "Together AI Blog",
        "url": "https://www.together.ai/blog/rss.xml",
        "type": "rss",
        "category": "blog"
    },
    # Simon Willison (A3 — independent, high signal on LLM tooling & security)
    {
        "name": "Simon Willison",
        "url": "https://simonwillison.net/atom/everything/",
        "type": "rss",
        "category": "blog"
    },
    # Newsletters & independent voices (high-signal, weekly cadence)
    {
        "name": "Import AI (Jack Clark)",
        "url": "https://import-ai.substack.com/feed",
        "type": "rss",
        "category": "newsletter"
    },
    {
        "name": "Lil'Log (Lilian Weng)",
        "url": "https://lilianweng.github.io/index.xml",
        "type": "rss",
        "category": "blog"
    },
    {
        "name": "Stratechery",
        "url": "https://stratechery.com/feed/",
        "type": "rss",
        "category": "newsletter"
    },
    # Governance, Safety & Policy (fills the policy theme gap)
    {
        "name": "AI Now Institute",
        "url": "https://ainowinstitute.org/feed/",
        "type": "rss",
        "category": "policy"
    },
    {
        "name": "CSET Georgetown",
        "url": "https://cset.georgetown.edu/feed/",
        "type": "rss",
        "category": "policy"
    },
    {
        "name": "Lenny's Newsletter",
        "url": "https://www.lennysnewsletter.com/feed",
        "type": "rss",
        "category": "newsletter"
    }
]

# CSS-selector registry for sources marked type="web" in SOURCES.
#
# The fetcher's scrape_web_source() looks up the source name here; if no
# entry matches, it falls through to a generic class-name heuristic that
# rarely fires on modern sites.  Adding a `type: web` source to SOURCES
# without a matching entry here will silently scrape 0–5 articles per
# run.  Always add a selectors entry when you add a web source.
WEB_SCRAPE_SOURCES = [
    {
        "name": "Anthropic Engineering",
        "url": "https://www.anthropic.com/engineering",
        # Anthropic's /engineering page lists posts as <a> blocks each
        # containing a heading.  Pull the heading text as the title and
        # the surrounding <a>'s href as the link.
        "selectors": {
            "title": "h2, h3",
            "summary": "p",
            "link": "a",
        },
    },
    {
        "name": "Ethan Mollick",
        "url": "https://www.oneusefulthing.org",
        "selectors": {"title": "h2, h3", "summary": "p", "link": "a"},
    },
]
