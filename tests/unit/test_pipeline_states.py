from itertools import pairwise

import pytest
from app.domain.pipeline_states import (
    PIPELINE_TRANSITIONS,
    InvalidTransitionError,
    ensure_transition,
)


def test_happy_path_chain():
    chain = ["discovered", "resolved", "media_ready", "transcribing", "transcribed"]
    for cur, nxt in pairwise(chain):
        ensure_transition(cur, nxt)  # 不抛


def test_prepare_retry_from_failed():
    ensure_transition("failed", "resolved")


def test_skip_forward_rejected():
    with pytest.raises(InvalidTransitionError):
        ensure_transition("discovered", "transcribed")


def test_ready_is_terminal():
    with pytest.raises(InvalidTransitionError):
        ensure_transition("ready", "failed")


def test_unknown_state_rejected():
    with pytest.raises(InvalidTransitionError):
        ensure_transition("no_such_state", "resolved")


def test_all_prd_states_present():
    assert set(PIPELINE_TRANSITIONS) == {
        "discovered",
        "resolved",
        "media_ready",
        "transcribing",
        "transcribed",
        "extracting",
        "reviewing",
        "ready",
        "failed",
        "ignored",
    }
