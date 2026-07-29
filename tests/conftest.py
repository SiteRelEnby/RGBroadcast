"""Shared pytest fixtures.

``pytest-homeassistant-custom-component`` provides the ``hass`` fixture; this
just enables loading our custom integration from the repo.
"""

from __future__ import annotations

from collections.abc import Generator

import pytest


@pytest.fixture(autouse=True)
def auto_enable_custom_integrations(
    enable_custom_integrations: None,
) -> Generator[None]:
    """Allow Home Assistant to load the rgbroadcast custom component in tests."""
    yield
