"""Pytest path setup."""

from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

# API unit tests do not need Home Assistant's integration bootstrap.
CUSTOM_COMPONENTS = ROOT / "custom_components"
INTEGRATION = CUSTOM_COMPONENTS / "anthropic_usage_monitor"

custom_components = types.ModuleType("custom_components")
custom_components.__path__ = [str(CUSTOM_COMPONENTS)]
sys.modules.setdefault("custom_components", custom_components)

integration = types.ModuleType("custom_components.anthropic_usage_monitor")
integration.__path__ = [str(INTEGRATION)]
sys.modules.setdefault("custom_components.anthropic_usage_monitor", integration)
