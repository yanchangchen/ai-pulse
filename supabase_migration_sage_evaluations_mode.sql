-- Sage faithfulness: tag which answer mode produced the reply.
--
-- Phase 7 added the opt-in deep-research pipeline (Scout -> Fact-Checker
-- -> Analyst) alongside the fast single-call path.  Both share the rolling
-- chat-time faithfulness baseline on the Quality Evaluation page; without
-- a mode tag the two populations are indistinguishable, and Phase 8's
-- regression gate wants to compare them separately (deep replies are
-- pre-checked, so their supported-claim profile should differ).
--
-- answer_mode: 'fast' | 'deep'.  NULL = legacy rows recorded before this
-- migration (the page renders them as "untagged").  The insert path
-- retries without the column on pre-migration databases, so judging never
-- breaks while the migration is pending.
--
-- Idempotent: safe to re-run.

ALTER TABLE sage_evaluations
  ADD COLUMN IF NOT EXISTS answer_mode VARCHAR(20);

COMMENT ON COLUMN sage_evaluations.answer_mode IS
  'fast | deep (Phase 7); NULL = pre-tagging legacy rows';

-- Verify
SELECT answer_mode, COUNT(*) FROM sage_evaluations GROUP BY answer_mode;