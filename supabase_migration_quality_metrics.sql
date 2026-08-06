-- Migration: Add Grounding, Structural Compliance, Coverage, and Temporal Coherence scores
ALTER TABLE quality_evaluations
    ADD COLUMN IF NOT EXISTS grounding_score FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS structural_compliance_score FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS coverage_score FLOAT DEFAULT 1.0,
    ADD COLUMN IF NOT EXISTS temporal_coherence_score FLOAT DEFAULT 1.0;
