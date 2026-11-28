"""Routing/contract tests for the Phase 7 Sage agent task types.

Mirrors tests/test_gateway_evaluate.py: the three deep-research agents
(sage_scout, sage_factcheck, sage_analyst) must route like sage_chat
(Qwen primary, Gemini safety net), pass prompts verbatim, carry no output
schema, and never fall back to a deterministic stand-in.
"""

from core.ai_gateway.contracts import AITaskRequest, TaskType
from core.ai_gateway.gateway import ModelGateway

AGENT_TASKS = [TaskType.SAGE_SCOUT, TaskType.SAGE_FACTCHECK, TaskType.SAGE_ANALYST]


def test_agent_policies_mirror_sage_chat():
    gw = ModelGateway()
    chat = gw._get_routing_policy(TaskType.SAGE_CHAT)
    for task in AGENT_TASKS:
        policy = gw._get_routing_policy(task)
        assert policy["primary"] == chat["primary"] == "qwen3.8-flash"
        assert policy["fallback"] == chat["fallback"]
        assert policy["deterministic_fallback"] is False


def test_agent_prompts_are_verbatim():
    gw = ModelGateway()
    for task in AGENT_TASKS:
        req = AITaskRequest(task=task, input="AGENT PROMPT VERBATIM")
        assert gw._build_prompt(req) == "AGENT PROMPT VERBATIM"


def test_agents_have_no_default_schema():
    gw = ModelGateway()
    for task in AGENT_TASKS:
        assert gw._get_schema(task) is None


def test_agents_listed_in_registry_tasks():
    gw = ModelGateway()
    registry = gw.routing_config["model_registry"]
    for model in ("qwen3.8-flash", "gemini-3.6-flash", "gemini-3.8-flash"):
        for task in AGENT_TASKS:
            assert task.value in registry[model]["tasks"]