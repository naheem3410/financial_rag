"""Standing structural constraint: `llm/client.py` is the only permitted seam
to a provider SDK.

`llm_call_counter` / `payload_capture` (tests/conftest.py) only observe calls
that cross `LLMClient.complete`. If any component imported a provider SDK
directly and called it, those fixtures would report zero calls while a real
model was invoked every time — `test_citation_no_llm` and the other three
load-bearing "zero LLM calls" tests would go green for the wrong reason. This
test closes that hole with static analysis instead of a runtime fixture: it
does not depend on the component under test being imported or executed at
all, so it cannot be bypassed by a module that never gets exercised in a
test run.

Unlike the Phase 0 acceptance tests, this must PASS now and stay passing —
it is a standing constraint, not a target for Phase 1+ to satisfy later.
"""
from __future__ import annotations

import ast
from pathlib import Path

# Known provider SDKs. A future provider added to llm/client.py's dependencies
# that isn't listed here should be added at the same time.
FORBIDDEN_SDK_ROOTS = {
    "anthropic",
    "openai",
    "google.generativeai",
    "litellm",
    "cohere",
    "mistralai",
    "groq",
    "replicate",
    "together",
    "ollama",
    "vertexai",
    "boto3",  # commonly used to reach Bedrock-hosted models
}

# llm/ is the one permitted seam. Everything else here is repo/tooling
# noise, never a source of a real import.
EXEMPT_TOP_LEVEL_DIRS = {"llm"}
EXCLUDED_DIR_NAMES = {".git", ".pytest_cache", "__pycache__", ".venv", "venv", "node_modules"}

REPO_ROOT = Path(__file__).resolve().parent.parent


def _iter_scanned_source_files(root: Path):
    for path in root.rglob("*.py"):
        if any(part in EXCLUDED_DIR_NAMES for part in path.parts):
            continue
        rel = path.relative_to(root)
        if rel.parts and rel.parts[0] in EXEMPT_TOP_LEVEL_DIRS:
            continue
        yield path


def _imported_module_names(path: Path) -> list[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    names: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            names.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module:
            names.append(node.module)
    return names


def _is_forbidden(module_name: str) -> bool:
    return any(
        module_name == forbidden or module_name.startswith(forbidden + ".")
        for forbidden in FORBIDDEN_SDK_ROOTS
    )


def test_no_direct_sdk_imports():
    violations = []
    for path in _iter_scanned_source_files(REPO_ROOT):
        for module_name in _imported_module_names(path):
            if _is_forbidden(module_name):
                violations.append(f"{path.relative_to(REPO_ROOT)}: imports {module_name!r}")

    assert violations == [], (
        "Provider SDKs may only be imported inside llm/ — LLMClient.complete "
        "is the only permitted seam. "
        "Route these calls through llm/client.py instead:\n" + "\n".join(violations)
    )


def test_scan_actually_covers_files_outside_llm():
    """Meta-check: if the scan silently found zero files (e.g. REPO_ROOT is
    wrong on some platform), test_no_direct_sdk_imports would pass vacuously.
    tests/ itself is always present and outside llm/, so this must be nonzero."""
    scanned = list(_iter_scanned_source_files(REPO_ROOT))
    assert any(p.name == "test_no_direct_sdk_imports.py" for p in scanned)


def test_forbidden_import_is_actually_detected(tmp_path, monkeypatch):
    """Meta-check on the detector itself: a file that does import a forbidden
    SDK must be caught, proving the AST walk and prefix match are not
    silently no-ops."""
    offender = tmp_path / "would_be_offender.py"
    offender.write_text("import openai\n", encoding="utf-8")

    names = _imported_module_names(offender)

    assert names == ["openai"]
    assert _is_forbidden("openai") is True
    assert _is_forbidden("openai.types") is True
    assert _is_forbidden("openai_compatible_thing") is False
