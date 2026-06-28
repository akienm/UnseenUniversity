"""Vetinari — meta-orchestrator device for the agent collective.

Lord Vetinari manages the whole rack without anyone noticing. He knows what
every factory and agent is doing, holds owner_id for factories without a more
specific owner, makes high-level resource allocation decisions, and reports to
Akien when human decisions are required.

PA2.0 Layer 3: factory lifecycle management, agent health rollup, budget
reallocation, cross-factory goal tracking. See C-prescient-agents-pa20 and
G-factory-of-factories.
"""
from unseen_university.devices.vetinari.device import VetinariDevice

__all__ = ["VetinariDevice"]
