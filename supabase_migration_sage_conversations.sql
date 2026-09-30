-- Migration: Sage Persistent Conversations Table
-- Run in Supabase SQL Editor:
-- SQL Editor > New Query > Paste content > Run

CREATE TABLE IF NOT EXISTS sage_conversations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  title VARCHAR(255) NOT NULL,
  theme_filter VARCHAR(255),
  period_label VARCHAR(100),
  messages JSONB NOT NULL DEFAULT '[]'::jsonb,
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Index for fast retrieval ordered by last update
CREATE INDEX IF NOT EXISTS idx_sage_conversations_updated_at ON sage_conversations(updated_at DESC);

-- Enable Row Level Security (RLS)
ALTER TABLE sage_conversations ENABLE ROW LEVEL SECURITY;

-- Permissive policies for full CRUD
CREATE POLICY "sage_conversations_select_policy" ON sage_conversations FOR SELECT USING (true);
CREATE POLICY "sage_conversations_insert_policy" ON sage_conversations FOR INSERT WITH CHECK (true);
CREATE POLICY "sage_conversations_update_policy" ON sage_conversations FOR UPDATE USING (true);
CREATE POLICY "sage_conversations_delete_policy" ON sage_conversations FOR DELETE USING (true);
