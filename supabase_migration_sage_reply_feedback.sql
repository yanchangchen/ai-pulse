-- Phase 8.3: human feedback on individual Sage replies.
--
-- The chat-time judge grades every reply automatically; this table adds
-- the human signal: a thumbs rating per reply plus an optional free-text
-- correction.  Down-ratings with corrections feed the Phase 8.1 exemplar
-- corpus as negative examples ("READER CORRECTION" lines), so the loop
-- learns from people as well as the judge.
--
-- answer_excerpt is a short prefix of the reply (the full text lives in
-- sage_conversations / sage_evaluations); question + conversation_id
-- locate the reply.  No FK to sage_evaluations: judgments and ratings
-- arrive independently and join loosely on (conversation_id, question).
--
-- Idempotent: safe to re-run.

CREATE TABLE IF NOT EXISTS sage_reply_feedback (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  conversation_id TEXT,
  question TEXT NOT NULL,
  answer_excerpt TEXT,
  rating VARCHAR(10) NOT NULL,          -- 'up' | 'down'
  correction TEXT,                      -- optional free text (mostly on 'down')
  answer_mode VARCHAR(20),              -- fast | deep (NULL = untagged)
  created_at TIMESTAMP WITH TIME ZONE DEFAULT NOW()
);

CREATE INDEX IF NOT EXISTS idx_sage_reply_feedback_created
  ON sage_reply_feedback (created_at DESC);
CREATE INDEX IF NOT EXISTS idx_sage_reply_feedback_rating
  ON sage_reply_feedback (rating);

ALTER TABLE sage_reply_feedback ENABLE ROW LEVEL SECURITY;

DROP POLICY IF EXISTS "sage_reply_feedback_read" ON sage_reply_feedback;
CREATE POLICY "sage_reply_feedback_read" ON sage_reply_feedback
  FOR SELECT USING (true);

DROP POLICY IF EXISTS "sage_reply_feedback_write" ON sage_reply_feedback;
CREATE POLICY "sage_reply_feedback_write" ON sage_reply_feedback
  FOR INSERT WITH CHECK (true);

DROP POLICY IF EXISTS "sage_reply_feedback_update" ON sage_reply_feedback;
CREATE POLICY "sage_reply_feedback_update" ON sage_reply_feedback
  FOR UPDATE USING (true) WITH CHECK (true);

GRANT SELECT, INSERT, UPDATE ON sage_reply_feedback TO anon;

-- Verify
SELECT rating, COUNT(*) FROM sage_reply_feedback GROUP BY rating;