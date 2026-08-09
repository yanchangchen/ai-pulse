-- Migration: Backfill existing articles with missing published_at or source_name
-- Execute this SQL query in your Supabase project dashboard:
-- SQL Editor > New Query > Paste content > Run

-- 1. Backfill published_at with created_at (crawl timestamp) if missing/null
UPDATE articles
SET published_at = created_at
WHERE published_at IS NULL;

-- 2. Backfill source_name from link domain if missing or empty
UPDATE articles
SET source_name = CASE
  WHEN link LIKE '%arxiv.org%' THEN 'ArXiv'
  WHEN link LIKE '%huggingface.co%' THEN 'Hugging Face Blog'
  WHEN link LIKE '%techcrunch.com%' THEN 'TechCrunch'
  WHEN link LIKE '%openai.com%' THEN 'OpenAI Blog'
  WHEN link LIKE '%anthropic.com%' THEN 'Anthropic Blog'
  WHEN link LIKE '%google%' OR link LIKE '%deepmind%' THEN 'Google AI Blog'
  ELSE 'Tracked Web Source'
END
WHERE source_name IS NULL OR source_name = '';
