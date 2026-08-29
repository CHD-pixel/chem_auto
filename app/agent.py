"""ChemAutoAgent — multi-agent architecture with sub_agents delegation.

Root agent coordinates 4 specialized sub-agents:
  - build_agent: OCR → extraction → code generation
  - test_agent:  connect → test → diagnose → fix
  - publish_agent: save to registry
  - invoke_agent: call published drivers

Root agent also has shared tools: edit_code, write_code, ask_user, SkillToolset.
"""

import pathlib

from google.adk.agents import Agent
from google.adk.apps.app import App, EventsCompactionConfig

from app.llm.client_factory import build_llm
from app.runtime.config import TEXT_MODEL

# ── Prompts ─────────────────────────────────────────────────────────

_PROMPTS = pathlib.Path(__file__).resolve().parent / "prompts"


def _read_prompt(*parts: str) -> str:
    return (_PROMPTS / pathlib.Path(*parts)).read_text(encoding="utf-8")


# ── Shared tools (available to root agent) ──────────────────────────

from app.tools.always_visible import ask_user, load_state_values
from app.tools.code_edit_tools import edit_code, write_code
from app.tools.manual_tools import (
    register_manual_pdf, process_web_upload, list_registered_manuals,
)

# ── SkillToolset ────────────────────────────────────────────────────

from app.tools import _build_skill_toolset

# ── Sub-agents ──────────────────────────────────────────────────────

from app.tools.build_tools import (
    run_manual_understanding, generate_code, check_function_coverage, merge_code,
)
from app.tools.test_tools import (
    list_serial_ports, verify_serial_port,
    connect_device, run_tests, fix_driver_code, confirm_result,
)
from app.tools.publish_tools import publish_current_driver
from app.tools.invoke_tools import (
    list_devices, list_serial_ports, verify_serial_port,
    get_device_info, get_all_device_functions, invoke_function, delete_device,
    save_plan, load_plan, list_plans, delete_plan,
    start_log, log_step, finish_log, list_logs, load_log,
)
from app.callbacks.model_callbacks import sanitize_model_response
from app.callbacks.file_filter import filter_file_parts

# Shared callback for all agents — strips non-text parts (file, inline_data)
# that text-only models like DeepSeek cannot process.
_FILE_FILTER = filter_file_parts

# ── Sub-agent factories ──────────────────────────────────────────────
# Use factory functions to avoid "agent already has a parent" errors.


def _create_build_agent() -> Agent:
    return Agent(
        name="build_agent",
        model=build_llm(TEXT_MODEL),
        description=(
            "Build a Python driver from a PDF manual. "
            "Handles OCR, data extraction, code generation, and coverage verification."
        ),
        instruction=_read_prompt("build", "build_agent_prompt.md"),
        tools=[
            register_manual_pdf, process_web_upload, list_registered_manuals,
            run_manual_understanding, generate_code, check_function_coverage, merge_code,
            edit_code, write_code,
        ],
        before_model_callback=_FILE_FILTER,
    )


def _create_test_agent() -> Agent:
    return Agent(
        name="test_agent",
        model=build_llm(TEXT_MODEL),
        description=(
            "Test a generated driver on real hardware. "
            "Handles device connection, test execution, failure diagnosis, and code fixes."
        ),
        instruction=_read_prompt("test", "test_agent_prompt.md"),
        tools=[
            list_serial_ports, verify_serial_port,
            connect_device, run_tests, fix_driver_code,
            confirm_result, edit_code, write_code,
            load_state_values,
        ],
        before_model_callback=_FILE_FILTER,
    )


def _create_publish_agent() -> Agent:
    return Agent(
        name="publish_agent",
        model=build_llm(TEXT_MODEL),
        description=(
            "Publish a tested driver to the cross-session registry. "
            "Requires all tests to have passed."
        ),
        instruction=_read_prompt("publish", "publish_agent_prompt.md"),
        tools=[publish_current_driver],
        before_model_callback=_FILE_FILTER,
    )


def _create_invoke_agent() -> Agent:
    return Agent(
        name="invoke_agent",
        model=build_llm(TEXT_MODEL),
        description=(
            "Call published driver functions on real instruments. "
            "Handles device discovery, function lookup, and execution with safety guardrails."
        ),
        instruction=_read_prompt("invoke", "invoke_agent_prompt.md"),
        tools=[
            list_devices, list_serial_ports, verify_serial_port,
            get_device_info, get_all_device_functions, invoke_function, delete_device,
            save_plan, load_plan, list_plans, delete_plan,
            start_log, log_step, finish_log, list_logs, load_log,
        ],
        before_model_callback=_FILE_FILTER,
    )


# ── Root Agent ──────────────────────────────────────────────────────

# Build skill toolset
_skill_toolset = _build_skill_toolset()

_root_tools = [ask_user, load_state_values, edit_code, write_code]
if _skill_toolset:
    _root_tools.append(_skill_toolset)

root_agent = Agent(
    name="chem_auto_agent",
    model=build_llm(TEXT_MODEL),
    description="Lab instrument automation agent. Coordinates build, test, publish, and invoke workflows.",
    instruction=_read_prompt("root", "root_prompt_v2.md"),
    tools=_root_tools,
    sub_agents=[_create_build_agent(), _create_test_agent(), _create_publish_agent(), _create_invoke_agent()],
    before_model_callback=filter_file_parts,
    after_model_callback=sanitize_model_response,
)

# Alias used by the adk CLI (`adk run app`) to discover the root agent
chem_auto_agent = root_agent

# ── App ─────────────────────────────────────────────────────────────

from app.plugins.error_boundary import ErrorBoundaryPlugin

app = App(
    name="app",
    root_agent=root_agent,
    plugins=[
        ErrorBoundaryPlugin(),
    ],
    events_compaction_config=EventsCompactionConfig(
        compaction_interval=200,
        overlap_size=3,
    ),
)
