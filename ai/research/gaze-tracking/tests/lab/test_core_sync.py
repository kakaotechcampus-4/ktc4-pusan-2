"""research 와 서버 코어가 같은 값을 쓰는지 검사한다.

- 서버(service)는 YAML 을 읽지 않고 gaze.config.EvidenceConfig 의 기본값을 쓴다.
  research 는 configs/evidence.yaml 로 실험한다. 둘이 어긋나면 실험한 값과 서버 값이 달라진다.
- 상태 · 방향 목록은 동점 처리 순서까지 같아야 한다 (코어는 gaze_lab 을 import 하지 않으므로 따로 둔다).
- gaze_lab.evidence.gaze 가 다시 내보내는 이름은 코어의 것과 같은 객체여야 한다 (사본이 아니라).
"""

from __future__ import annotations

import dataclasses

import gaze.core as core
import gaze_lab.evidence.gaze as device
from gaze.config import EvidenceConfig
from gaze_lab import schemas
from gaze_lab.config import load_config


def test_evidence_yaml_equals_the_core_defaults():
    assert dataclasses.asdict(load_config().evidence) == dataclasses.asdict(EvidenceConfig())


def test_gaze_lab_uses_the_core_config_class():
    assert type(load_config().evidence) is EvidenceConfig


def test_state_and_direction_lists_match_in_order():
    assert core.STATE_CLASSES == schemas.STATE_CLASSES
    assert core.GAZE_DIRECTIONS == schemas.GAZE_DIRECTIONS


def test_device_module_re_exports_the_core_objects():
    for name in device.__all__:
        if hasattr(core, name):
            assert getattr(device, name) is getattr(core, name), name
