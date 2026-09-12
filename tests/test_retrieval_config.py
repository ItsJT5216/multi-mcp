"""Tests for retrieval configuration in YAML config."""

import pytest
import yaml
from io import StringIO
from src.multimcp.yaml_config import MultiMCPConfig, RetrievalSettings


class TestRetrievalSettings:
    def test_defaults_disabled(self):
        settings = RetrievalSettings()
        assert settings.enabled is False
        assert settings.top_k == 15  # Phase 9: top_k default corrected from 10 to 15 (source plan line 496)
        assert settings.full_description_count == 3
        assert settings.anchor_tools == []

    def test_custom_values(self):
        settings = RetrievalSettings(
            enabled=True,
            top_k=5,
            full_description_count=2,
            anchor_tools=["github__get_me"],
        )
        assert settings.enabled is True
        assert settings.top_k == 5


class TestMultiMCPConfigRetrieval:
    def test_config_without_retrieval_key(self):
        """Existing configs without retrieval section should work."""
        config = MultiMCPConfig()
        assert config.retrieval.enabled is False

    def test_config_with_retrieval_section(self):
        config = MultiMCPConfig(retrieval=RetrievalSettings(enabled=True, top_k=7))
        assert config.retrieval.enabled is True
        assert config.retrieval.top_k == 7

    def test_yaml_roundtrip(self):
        """Config should survive YAML serialization/deserialization."""
        config = MultiMCPConfig(
            retrieval=RetrievalSettings(
                enabled=True,
                top_k=15,
                anchor_tools=["github__get_me", "exa__search"],
            )
        )
        dumped = config.model_dump()
        restored = MultiMCPConfig(**dumped)
        assert restored.retrieval.enabled is True
        assert restored.retrieval.top_k == 15
        assert len(restored.retrieval.anchor_tools) == 2

    def test_invalid_top_k_negative(self):
        """Negative top_k should raise validation error."""
        with pytest.raises(Exception):  # Pydantic ValidationError
            RetrievalSettings(top_k=-1)
