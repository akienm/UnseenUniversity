"""
Vetinari - the meta-orchestrator for the agent collective.
"""
from typing import Any, Dict, List
from devices.base import BaseShim
from tools.factory_manager import FactoryManager
from tools.trust_scoring import TrustScorer
from tools.budget_ledger import BudgetLedger
from tools.agent_health import AgentHealth


class Vetinari(BaseShim):
    """Vetinari - the meta-orchestrator."""

    def __init__(self, config: Dict[str, Any]):
        super().__init__(config)
        self.factory_manager = FactoryManager(config)
        self.trust_scorer = TrustScorer(config)
        self.budget_ledger = BudgetLedger(config)
        self.agent_health = AgentHealth(config)
        self.status = {
            "factories": {},
            "trust_scores": {},
            "budget": {},
            "health": {}
        }

    def process(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Process incoming data and make decisions."""
        # Handle factory lifecycle management
        if "factory_action" in data:
            return self._handle_factory_action(data)
        
        # Handle health rollup
        if "health_data" in data:
            return self._handle_health_data(data)
        
        # Handle budget reallocation
        if "budget_request" in data:
            return self._handle_budget_request(data)
        
        # Handle trust scoring
        if "trust_data" in data:
            return self._handle_trust_data(data)
        
        # Default response
        return {"status": "no_action_taken"}

    def _handle_factory_action(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle factory lifecycle management."""
        action = data["factory_action"]
        factory_id = data["factory_id"]
        
        if action == "create":
            return self.factory_manager.create_factory(factory_id, data.get("spec", {}))
        elif action == "halt":
            return self.factory_manager.halt_factory(factory_id)
        elif action == "retire":
            return self.factory_manager.retire_factory(factory_id)
        else:
            return {"error": f"Unknown factory action: {action}"}

    def _handle_health_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle agent health rollup."""
        health_data = data["health_data"]
        self.status["health"] = health_data
        # Check if any agent health is below threshold
        if self._should_escalate(health_data):
            return self._escalate_to_akien(health_data)
        return {"status": "health_processed"}

    def _handle_budget_request(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle budget reallocation."""
        return self.budget_ledger.reallocate_budget(data.get("allocation", {}))

    def _handle_trust_data(self, data: Dict[str, Any]) -> Dict[str, Any]:
        """Handle trust scoring."""
        trust_data = data["trust_data"]
        self.status["trust_scores"] = trust_data
        return self.trust_scorer.update_scores(trust_data)

    def _should_escalate(self, health_data: Dict[str, Any]) -> bool:
        """Determine if health data requires escalation to Akien."""
        # Simple threshold check for now
        threshold = self.config.get("escalation_threshold", 0.5)
        for agent_id, health in health_data.items():
            if health.get("score", 1.0) < threshold:
                return True
        return False

    def _escalate_to_akien(self, health_data: Dict[str, Any]) -> Dict[str, Any]:
        """Escalate to Akien when health drops below threshold."""
        return {
            "escalation": "to_akien",
            "reason": "agent_health_below_threshold",
            "data": health_data
        }

    def get_status(self) -> Dict[str, Any]:
        """Get the current status of Vetinari."""
        return self.status
