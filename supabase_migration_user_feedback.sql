-- Migration: User Feedback and Roadmap Table
-- Execute this SQL query in your Supabase SQL Editor:
-- SQL Editor > New Query > Paste content > Run

CREATE TABLE IF NOT EXISTS user_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  category VARCHAR(50) NOT NULL, -- 'feature', 'bug', 'ux'
  title VARCHAR(255) NOT NULL,
  description TEXT NOT NULL,
  status VARCHAR(50) DEFAULT 'open', -- 'open', 'in_progress', 'resolved', 'closed'
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW(),
  updated_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

-- Indexes for fast querying & filtering
CREATE INDEX IF NOT EXISTS idx_user_feedback_category ON user_feedback(category);
CREATE INDEX IF NOT EXISTS idx_user_feedback_status ON user_feedback(status);
CREATE INDEX IF NOT EXISTS idx_user_feedback_created_at ON user_feedback(created_at DESC);

-- Enable Row Level Security (RLS)
ALTER TABLE user_feedback ENABLE ROW LEVEL SECURITY;

-- Policies for public reading, inserting, and updating status
CREATE POLICY "user_feedback_select_policy" ON user_feedback FOR SELECT USING (true);
CREATE POLICY "user_feedback_insert_policy" ON user_feedback FOR INSERT WITH CHECK (true);
CREATE POLICY "user_feedback_update_policy" ON user_feedback FOR UPDATE USING (true);
CREATE POLICY "user_feedback_delete_policy" ON user_feedback FOR DELETE USING (true);
