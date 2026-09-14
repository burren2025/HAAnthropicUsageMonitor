"""Regression checks for credential handling in the config flow."""

from __future__ import annotations

import ast
from pathlib import Path

CONFIG_FLOW = (
    Path(__file__).resolve().parents[1]
    / "custom_components"
    / "anthropic_usage_monitor"
    / "config_flow.py"
)


def _method(class_name: str, method_name: str) -> ast.FunctionDef | ast.AsyncFunctionDef:
    tree = ast.parse(CONFIG_FLOW.read_text(encoding="utf-8"))
    class_node = next(
        node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == class_name
    )
    return next(
        node
        for node in class_node.body
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)) and node.name == method_name
    )


def test_options_flow_never_reads_or_updates_the_admin_key() -> None:
    """Options must not display, validate, or replace the stored credential."""
    nodes = [
        _method("AnthropicUsageOptionsFlow", "async_step_init"),
        next(
            node
            for node in ast.parse(CONFIG_FLOW.read_text(encoding="utf-8")).body
            if isinstance(node, ast.FunctionDef) and node.name == "_options_schema"
        ),
    ]

    assert all(
        not any(
            isinstance(node, ast.Name) and node.id == "CONF_ADMIN_API_KEY"
            for node in ast.walk(item)
        )
        for item in nodes
    )


def test_reauth_updates_without_starting_a_second_reload() -> None:
    """The config-entry listener owns the single reload after reauth."""
    method = _method("AnthropicUsageConfigFlow", "async_step_reauth_confirm")
    called_attributes = {
        node.func.attr
        for node in ast.walk(method)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
    }

    assert "async_update_and_abort" in called_attributes
    assert "async_update_reload_and_abort" not in called_attributes
    assert "async_reload" not in called_attributes


def test_authentication_failures_trigger_home_assistant_reauth() -> None:
    """Coordinator authentication failures must not degrade into partial data."""
    coordinator = CONFIG_FLOW.with_name("coordinator.py").read_text(encoding="utf-8")

    assert "raise ConfigEntryAuthFailed" in coordinator
    assert "except AnthropicAuthError:" in coordinator
