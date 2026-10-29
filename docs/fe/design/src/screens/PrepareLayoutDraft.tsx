import { useState } from 'react';
import { PrepareDraftCheckCard } from '@/ui/PrepareDraftCheckCard';
import { PrepareDraftFrame } from '@/ui/PrepareDraftFrame';
import { PrepareDraftMissionCard } from '@/ui/PrepareDraftMissionCard';
import { PrepareDraftScriptMode } from '@/ui/PrepareDraftScriptMode';
import type { ScriptMode } from '@/ui/ScriptModeChoice';
import { prepareDraftFixture } from './prepareDraftFixtures';
import { PITCH, type Navigate } from './shared';

const READING =
  '안녕하세요. 지금부터 발표를 시작하겠습니다. 오늘 준비한 내용을 차근차근 말씀드리겠습니다. 먼저 이번 발표에서 다룰 문제를 소개하고, 이어서 해결 방법을 설명드리겠습니다.';

export function PrepareLayoutDraft({
  stateId,
  onNavigate,
}: {
  stateId: string;
  onNavigate: Navigate;
}) {
  return <Draft key={stateId} fixture={prepareDraftFixture(stateId)} onNavigate={onNavigate} />;
}

/** 버튼은 고정 예시 상태와 설계서 화면만 바꾼다. 시간 측정·권한 요청·Take 생성은 없다. */
function Draft({
  fixture,
  onNavigate,
}: {
  fixture: ReturnType<typeof prepareDraftFixture>;
  onNavigate: Navigate;
}) {
  const [camera, setCamera] = useState(fixture.camera);
  const [mic, setMic] = useState(fixture.mic);
  const [gaze, setGaze] = useState(fixture.gaze);
  const [voice, setVoice] = useState(fixture.voice);
  const [mode, setMode] = useState<ScriptMode>(fixture.exam ? 'OFF' : 'HIGHLIGHT');
  const ready = camera && mic && gaze === 'DONE' && !fixture.starting;
  const reading = voice === 'READING';
  let hint = '점검이 끝났어요. 평소 목소리 측정은 선택이에요.';
  if (!camera) hint = '카메라·마이크를 켜면 점검을 시작할 수 있어요.';
  else if (!mic) hint = '마이크에 대고 한 마디 해 보세요.';
  else if (gaze !== 'DONE') hint = '시선 기준을 잡으면 연습을 시작할 수 있어요.';
  if (reading) hint = '문단을 평소 목소리로 읽고, 다 읽으면 “다 읽었어요”를 눌러 주세요.';
  if (fixture.permissionError) hint = '카메라와 마이크 사용을 허용해 주세요. (권한 오류 예시)';
  if (fixture.starting) hint = '연습을 여는 중… (시안 예시)';
  let gazeLabel = '기준을 잡아 주세요';
  let gazeAction = '시선 기준 잡기';
  if (gaze === 'DONE') {
    gazeLabel = '3/3 완료';
    gazeAction = '다시 잡기';
  }
  if (gaze === 'UNAVAILABLE') {
    gazeLabel = '시선 분석 사용 불가';
    gazeAction = '사용 불가';
  }
  let voiceLabel = '아직 재지 않았어요';
  let voiceAction = '목소리 재기';
  if (!mic) voiceLabel = '마이크 연결 후 가능';
  if (voice === 'READING') {
    voiceLabel = '크기 재는 중';
    voiceAction = '그만두기';
  }
  if (voice === 'DONE') {
    voiceLabel = '확인됨';
    voiceAction = '다시 읽기';
  }

  return (
    <PrepareDraftFrame
      title={PITCH}
      subtitle="자료 v2 · 대본 v2"
      badge="TAKE 3"
      onBack={() => onNavigate('pitch-create')}
      hint={hint}
      actions={
        <button
          type="button"
          className="prepare-draft-action prepare-draft-action-primary"
          disabled={!ready}
          onClick={() => onNavigate('stage', mode === 'OFF' ? 'exam' : stageState(mode))}
        >
          {mode === 'OFF' ? 'Take 3 실전 모드로 시작' : 'Take 3 시작하기'}
        </button>
      }
    >
      <p className="prepare-draft-review-notice">
        PM 검토용 시안 · 실제 장비와 서버를 사용하지 않아요.
      </p>
      <div className="prepare-draft-content">
        <div className="prepare-draft-camera prepare-draft-camera-example">
          <p>
            {camera
              ? '카메라 미리보기 예시 · 실제 촬영 없음'
              : '장비를 켜면 카메라 미리보기가 보여요.'}
          </p>
        </div>
        <div className="prepare-draft-controls">
          <PrepareDraftCheckCard
            title="장비 준비"
            action={
              !camera && (
                <button
                  type="button"
                  className="prepare-draft-action prepare-draft-action-primary"
                  disabled={fixture.starting}
                  onClick={() => {
                    setCamera(true);
                    setMic(true);
                    setGaze('IDLE');
                  }}
                >
                  카메라·마이크 켜기
                </button>
              )
            }
            rows={[
              {
                id: 'camera',
                name: '카메라',
                label: camera ? '연결됨' : '영상 없음',
                done: camera,
                selection: (
                  <DeviceChoice
                    label="카메라"
                    disabled={!camera || reading || fixture.starting}
                    long={fixture.longDevices}
                  />
                ),
              },
              {
                id: 'mic',
                name: '마이크',
                label: mic ? '마이크 입력 확인됨' : '연결 전',
                done: mic,
                selection: (
                  <DeviceChoice
                    label="마이크"
                    disabled={!camera || reading || fixture.starting}
                    long={fixture.longDevices}
                    onChange={() => setMic(true)}
                  />
                ),
                trailing: mic && (
                  <span className="prepare-draft-example-meter" aria-label="예시 음량" />
                ),
              },
              {
                id: 'gaze',
                name: '시선 기준',
                label: gazeLabel,
                done: gaze === 'DONE',
                action: {
                  text: gazeAction,
                  onClick: () => setGaze('DONE'),
                  disabled: !camera || gaze === 'UNAVAILABLE' || reading || fixture.starting,
                  primary: gaze === 'IDLE',
                },
              },
              {
                id: 'voice',
                name: '평소 목소리',
                label: voiceLabel,
                done: voice === 'DONE',
                optional: true,
                action: {
                  text: voiceAction,
                  onClick: () => setVoice(reading ? 'IDLE' : 'READING'),
                  disabled: !mic || fixture.starting,
                },
              },
            ]}
          >
            {reading && (
              <div className="prepare-draft-voice-panel">
                <p className="prepare-draft-voice-script">{READING}</p>
                <div className="prepare-draft-voice-progress">
                  <span className="prepare-draft-example-meter" aria-label="예시 음량" />
                  <p>목소리가 들려요 · 계속 읽어 주세요 (예시)</p>
                  <button
                    type="button"
                    className="prepare-draft-action"
                    onClick={() => setVoice('DONE')}
                  >
                    다 읽었어요
                  </button>
                </div>
              </div>
            )}
            {voice === 'DONE' && (
              <p className="prepare-draft-notices" role="status">
                평소 목소리 확인 완료 예시예요. 실제 소리를 측정하지 않았어요.
              </p>
            )}
            {fixture.permissionError && (
              <p className="prepare-draft-notices" role="status">
                카메라와 마이크를 사용할 수 없는 상태의 예시예요.
              </p>
            )}
          </PrepareDraftCheckCard>
          <PrepareDraftMissionCard
            info={fixture.info}
            exam={mode === 'OFF'}
            compact={voice !== 'IDLE' || fixture.permissionError}
          />
          <PrepareDraftScriptMode value={mode} onChange={setMode} disabled={fixture.starting} />
        </div>
      </div>
    </PrepareDraftFrame>
  );
}

function stageState(mode: ScriptMode) {
  return mode === 'KEYWORD' ? 'keyword' : 'default';
}

function DeviceChoice({
  label,
  disabled,
  long,
  onChange,
}: {
  label: string;
  disabled: boolean;
  long: boolean;
  onChange?: () => void;
}) {
  const [value, setValue] = useState('default');
  const name = long
    ? `초고해상도 외장 ${label} Professional Conference Device — USB Interface (매우 긴 장치 이름 예시)`
    : `기본 ${label}`;
  return (
    <label>
      <span className="sr-only">{label}</span>
      <select
        className="prepare-draft-select"
        value={value}
        disabled={disabled}
        title={value === 'default' ? name : `외장 ${label} 예시`}
        onChange={(event) => {
          setValue(event.target.value);
          onChange?.();
        }}
      >
        <option value="default">{name}</option>
        <option value="external">외장 {label} 예시</option>
      </select>
    </label>
  );
}
