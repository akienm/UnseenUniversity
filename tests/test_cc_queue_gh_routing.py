"""Tests for cc_queue._gh_repo_for — GitHub repo routing by ticket type."""

import sys
import os

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "lab", "claudecode"))

from cc_queue import _gh_repo_for, _IGOR_REPO, _ADC_REPO


def _ticket(**kwargs) -> dict:
    base = {"worker": "claude", "tags": []}
    base.update(kwargs)
    return base


class TestGhRepoFor:
    def test_igor_worker_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(worker="igor")) == _IGOR_REPO

    def test_cognition_tag_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(tags=["Cognition"])) == _IGOR_REPO

    def test_memory_tag_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(tags=["Memory"])) == _IGOR_REPO

    def test_twm_tag_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(tags=["TWM"])) == _IGOR_REPO

    def test_habits_tag_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(tags=["Habits"])) == _IGOR_REPO

    def test_engrams_tag_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(tags=["Engrams"])) == _IGOR_REPO

    def test_narrative_engine_tag_routes_to_theigors(self):
        assert _gh_repo_for(_ticket(tags=["NarrativeEngine"])) == _IGOR_REPO

    def test_infrastructure_tag_routes_to_adc(self):
        assert _gh_repo_for(_ticket(tags=["Infrastructure"])) == _ADC_REPO

    def test_swarm_tag_routes_to_adc(self):
        assert _gh_repo_for(_ticket(tags=["Swarm"])) == _ADC_REPO

    def test_no_tags_claude_worker_routes_to_adc(self):
        assert _gh_repo_for(_ticket()) == _ADC_REPO

    def test_mixed_tags_igor_wins(self):
        assert _gh_repo_for(_ticket(tags=["Infrastructure", "Cognition"])) == _IGOR_REPO

    def test_tag_matching_is_case_insensitive(self):
        assert _gh_repo_for(_ticket(tags=["COGNITION"])) == _IGOR_REPO

    def test_empty_tags_routes_to_adc(self):
        assert _gh_repo_for(_ticket(tags=[])) == _ADC_REPO

    def test_none_tags_routes_to_adc(self):
        assert _gh_repo_for(_ticket(tags=None)) == _ADC_REPO
