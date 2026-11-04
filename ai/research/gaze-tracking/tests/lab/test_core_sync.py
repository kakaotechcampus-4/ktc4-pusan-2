"""기준 구현(gaze_lab)과 서버 판정 모듈(gaze)이 같은 이름 · 값을 쓰는지 검사한다.

- 1초 기록을 만드는 값(configs/evidence.yaml)은 엔진 값뿐이다. 서버 판정 기준값은 gaze.config 에만 둔다
  (gaze-tracking.md 4-3). YAML 에 판정 값이 다시 생기면 두 벌이 된다.
- 상태 · 방향 목록은 동점 처리 순서까지 같아야 한다 (서버 모듈은 gaze_lab 을 import 하지 않으므로 따로 둔다).
- 기준 구현이 만드는 1초 기록을 서버 모듈이 그대로 읽는다.
"""

from __future__ import annotations

import dataclasses
from typing import get_args

import pitch_coach_ai.features.gaze.core as core
from pitch_coach_ai.features.gaze.config import GazeConfig
from pitch_coach_ai.features.gaze.schemas import Direction, SampleState
from gaze_lab import schemas
from gaze_lab.config import EvidenceConfig, load_config
from gaze_lab.evidence.gaze import SAMPLE_STATES


def test_evidence_yaml_holds_only_the_slicing_values():
    assert dataclasses.asdict(load_config().evidence) == dataclasses.asdict(EvidenceConfig())
    assert set(dataclasses.asdict(EvidenceConfig())) == {
        "slice_ms",
        "min_frames_per_slice",
        "slice_vote_threshold",
    }


def test_no_server_threshold_lives_in_the_engine_config():
    assert not set(dataclasses.asdict(EvidenceConfig())) & set(GazeConfig.model_fields)


def test_state_and_direction_lists_match_in_order():
    assert core.MEASURED_STATES == schemas.STATE_CLASSES
    assert get_args(SampleState) == SAMPLE_STATES
    assert get_args(Direction) == schemas.GAZE_DIRECTIONS
