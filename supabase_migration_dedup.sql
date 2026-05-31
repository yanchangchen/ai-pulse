-- Migration: Add unique constraint on articles for deduplication
-- This ensures articles are deduplicated by content_hash and theme_name

-- Add unique constraint on articles table
ALTER TABLE articles ADD CONSTRAINT articles_content_hash_theme_unique 
  UNIQUE(content_hash, theme_name);

-- Create index for faster lookups
CREATE INDEX IF NOT EXISTS idx_articles_content_hash ON articles(content_hash);

-- Log
SELECT 'Deduplication constraint added successfully' as status;
