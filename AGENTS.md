# AGENTS.md — Agent & Harness Guidelines for AI Pulse

This document outlines key operational constraints and testing workflows for AI coding agents and automated harnesses working on the **AI Pulse** codebase.

---

## ⚡ Critical Testing Rule for AI Harnesses

> ### ⚠️ HARNESS DIRECTIVE: NEVER RUN THE FULL SUITE ON ROUTINE EDITS
> - The full test suite has grown to **444+ tests** and takes **~90 seconds** to complete.
> - Running `pytest tests/` after small edits creates excessive latency and wastes time.
> - **During active development and iteration, harnesses MUST ONLY run the 1–2 test files directly touching the changed code (< 0.5s execution time).**
> - Reserve the full `python -m pytest tests/` suite strictly as a final sanity check before declaring a task complete or submitting a PR.

---

## Targeted Test Cheat Sheet

| Component Modified | Targeted Test Command | Typical Runtime |
|---|---|---|
| **Sage Agent & Persona** | `python -m pytest tests/test_sage_agent.py` | ~0.2s |
| **Sage Conversations** | `python -m pytest tests/test_sage_conversations.py` | ~0.1s |
| **Both Sage Suites** | `python -m pytest tests/test_sage_agent.py tests/test_sage_conversations.py` | ~0.3s |
| **Supabase Client & Persistence** | `python -m pytest tests/test_supabase_client.py` | ~0.2s |
| **Theme Classifier & TF-IDF** | `python -m pytest tests/test_classifier.py tests/test_tfidf_classifier.py` | ~0.3s |
| **Summariser & Synthesis** | `python -m pytest tests/test_summariser.py` | ~0.3s |
| **Model Gateway & Health** | `python -m pytest tests/test_gateway_health.py tests/test_gateway_evaluate.py` | ~0.5s |
| **Quality Evaluator** | `python -m pytest tests/test_evaluator.py` | ~1.5s |
| **Filter by Feature Keyword** | `python -m pytest tests/ -k "sage"` or `tests/ -k "feedback"` | ~0.3s |
| **Single Test Function** | `python -m pytest tests/test_sage_conversations.py::test_supabase_preferred_when_available` | ~0.05s |

---

## Other Important Harness Guidelines

1. **Test Location**: Always run pytest targeting `tests/` explicitly (`python -m pytest tests/...`). The root-level scripts (`test_supabase.py`, `test_backfill.py`, etc.) are standalone manual test scripts, not pytest fixtures.
2. **Supabase Isolation**: `tests/conftest.py` automatically stubs `SupabaseManager` to offline mode (`is_available() == False`) so unit tests never accidentally write to or corrupt production tables. Tests asserting Supabase behavior must mock `core.supabase_client.get_supabase_manager`.
3. **LLM Quota Reset**: `tests/conftest.py` automatically resets the `LLMClient` quota flag before and after each test.
4. **Primary Documentation**: See [CLAUDE.md](file:///c:/claude/ai-pulse/CLAUDE.md) for full project architecture, LLM gateway policies, and data pipeline details.
