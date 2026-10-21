"""Relevance ranker with specificity-based tiebreaking.

Ranks tools by score (descending) with most-specific-first tiebreaking
for tools with similar scores. Exploits LLM primacy bias (1.3-3.4x).
"""

from __future__ import annotations

from .models import ScoredTool

# Scores within this tolerance are considered tied
_SCORE_TOLERANCE = 0.05


def _get_specificity(scored: ScoredTool) -> int:
    """Count input properties as a specificity proxy."""
    schema = scored.tool_mapping.tool.inputSchema
    if isinstance(schema, dict):
        props = schema.get("properties", {})
        if isinstance(props, dict):
            return len(props)
    return 0


class RelevanceRanker:
    """Ranks scored tools by relevance with specificity tiebreaking."""

    def rank(self, tools: list[ScoredTool]) -> list[ScoredTool]:
        """Rank tools by score descending, tiebreak by specificity descending.

        Two tools are "tied" if their scores differ by less than SCORE_TOLERANCE.
        Among tied tools, the one with more inputSchema properties ranks first.
        """
        if not tools:
            return []

        by_score = sorted(tools, key=lambda t: (-t.score, t.tool_key))
        ranked: list[ScoredTool] = []
        tied_group: list[ScoredTool] = []
        group_score: float | None = None

        for tool in by_score:
            if group_score is None or abs(group_score - tool.score) < _SCORE_TOLERANCE:
                tied_group.append(tool)
                if group_score is None:
                    group_score = tool.score
                continue

            ranked.extend(
                sorted(tied_group, key=lambda t: (-_get_specificity(t), t.tool_key))
            )
            tied_group = [tool]
            group_score = tool.score

        ranked.extend(
            sorted(tied_group, key=lambda t: (-_get_specificity(t), t.tool_key))
        )
        return ranked
