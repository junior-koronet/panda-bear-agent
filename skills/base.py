"""
Panda Bear — Skill Base
All agent capabilities are Skills. Every Skill is reusable, testable, and logged.
"""

from abc import ABC, abstractmethod
from datetime import datetime
from typing import Any


class Skill(ABC):
    """
    Base class for all Panda Bear skills.
    Each skill represents ONE capability the agent can invoke.
    """

    name: str = "unnamed_skill"
    description: str = "No description provided."
    category: str = "general"

    def __init__(self, memory=None):
        self._memory = memory

    def execute(self, **kwargs) -> dict:
        """
        Execute the skill. Returns a dict with:
        - success: bool
        - result: Any (the outcome)
        - reasoning: str (why the agent made this choice)
        - errors: list (any issues encountered)
        """
        started_at = datetime.now().isoformat()
        try:
            result = self._run(**kwargs)
            if self._memory and result.get("decision"):
                self._memory.record_decision(
                    skill_name=self.name,
                    decision=result.get("decision", ""),
                    reasoning=result.get("reasoning", ""),
                    context=kwargs,
                    outcome="success",
                    confidence=result.get("confidence", 1.0),
                )
            result["success"] = result.get("success", True)
            result["skill"] = self.name
            result["executedAt"] = started_at
            return result
        except Exception as e:
            error_result = {
                "success": False,
                "skill": self.name,
                "error": str(e),
                "executedAt": started_at,
            }
            if self._memory:
                self._memory.record_decision(
                    skill_name=self.name,
                    decision="FAILED",
                    reasoning=str(e),
                    context=kwargs,
                    outcome="error",
                    confidence=0.0,
                )
            return error_result

    @abstractmethod
    def _run(self, **kwargs) -> dict:
        """Implement the actual skill logic here."""
        pass

    def __repr__(self):
        return f"<Skill:{self.name}>"
