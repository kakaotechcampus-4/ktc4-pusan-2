// 제품 리허설 무대(`frontend/src/features/rehearsal/stage/RehearsalPage.tsx`, `ScriptPane.tsx`)의 설계서 샘플.
import { useEffect, useState } from 'react';
import { Button } from '@/ui/Button';
import type { Navigate } from './shared';
import '@/ui/stage.css';

/**
 * 제품 RehearsalPage의 무대 DOM을 같은 클래스로 그린다. 배치 · 색은 src/ui/stage.css가 정한다.
 * 클래스 이름은 ScreenPreview.test가 stage.css에 있는지 확인한다.
 * 슬라이드는 ← → · Space로 넘긴다(제품과 같은 키). 끝내기는 마지막 장에서만 열린다(제품 #697).
 */
const PAGES = 12;

/**
 * 발표를 시작하기 전 무대 위 안내(제품 StartGateNotice · startGateText). 상태 ID → 문구 · 막혔는지.
 * 막힌 것(stuck)에만 "다시 시도" · "장치 점검으로"를 둔다. 그동안 시계 · 기록 · 끝내기는 멈춰 있다
 */
const START_GATES: Record<string, { text: string; stuck: boolean }> = {
  opening: { text: '카메라와 마이크를 여는 중이에요', stuck: false },
  connecting: { text: '말하기 분석 서버에 연결하는 중이에요', stuck: false },
  'connect-slow': {
    text: '말하기 분석 서버에 연결하지 못했어요. 연결되면 바로 시작해요.',
    stuck: true,
  },
  'no-mic': {
    text: '마이크 사용을 허용해 주세요. 주소창 왼쪽 자물쇠에서 바꿀 수 있어요. 마이크가 있어야 발표를 시작할 수 있어요.',
    stuck: true,
  },
  busy: {
    text: '다른 기기나 창에서 연습이 진행 중이에요. 그 연습을 끝낸 뒤 다시 시도해 주세요.',
    stuck: true,
  },
};
export function StageScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const exam = stateId === 'exam' || stateId === 'exam-confirm';
  const keyword = stateId === 'keyword' || stateId === 'keyword-confirm';
  const confirming =
    stateId === 'confirm-end' || stateId === 'exam-confirm' || stateId === 'keyword-confirm';
  // 종료 확인은 마지막 장에서만 열 수 있으니 그 상태들은 마지막 장에서 시작한다
  const [slide, setSlide] = useState(stateId === 'last-slide' || confirming ? PAGES : 3);
  const onLastSlide = slide >= PAGES;
  const gate = START_GATES[stateId];
  useEffect(() => {
    function move(event: KeyboardEvent) {
      if (event.key === 'ArrowRight' || event.key === ' ') setSlide((n) => Math.min(PAGES, n + 1));
      else if (event.key === 'ArrowLeft') setSlide((n) => Math.max(1, n - 1));
      else return;
      event.preventDefault();
    }
    window.addEventListener('keydown', move);
    return () => window.removeEventListener('keydown', move);
  }, []);
  let scriptMode = 'HIGHLIGHT';
  let confirmState = 'confirm-end';
  if (exam) {
    scriptMode = 'OFF';
    confirmState = 'exam-confirm';
  } else if (keyword) {
    scriptMode = 'KEYWORD';
    confirmState = 'keyword-confirm';
  }
  return (
    // 무대는 창 높이를 채운다(미리보기 iframe의 높이가 곧 창 높이). 아주 낮으면 24rem에서 멈춘다
    <div className="flex h-dvh min-h-[24rem] flex-col">
      <div className="rehearsal-page">
        <div className="rehearsal-content">
          <h1 className="sr-only">발표 연습 · Take 3</h1>
          <div className="rehearsal-frame">
            <div
              className="stage stage-live"
              data-script-mode={scriptMode}
              data-gaze={gate ? 'UNKNOWN' : 'AUDIENCE'}
              data-mode={exam ? 'EXAM' : 'COACHING'}
            >
              {gate && (
                <div className="start-gate" role="status" data-stuck={gate.stuck || undefined}>
                  <div className="box">
                    <p>{gate.text}</p>
                    {gate.stuck && (
                      <div className="actions">
                        <Button onClick={() => onNavigate('stage', 'default')}>다시 시도</Button>
                        <Button
                          variant="secondary"
                          onClick={() => onNavigate('prepare-mission', 'default')}
                        >
                          장치 점검으로
                        </Button>
                      </div>
                    )}
                  </div>
                </div>
              )}
              <header className="stage-head">
                <span className="elapsed">{gate ? '00:00' : '02:14'}</span>
                <span className="limit">/ 05:00</span>
                <div className="bar" data-over="false">
                  <i style={{ width: '45%' }} />
                </div>
                <span className="meta">남은 02:46</span>
                <span className="slide-no">
                  SLIDE {slide} / {PAGES}
                </span>
              </header>
              <div className="viewport">
                <div className="slide">
                  <span className="placeholder">SLIDE {slide} · 16:9</span>
                </div>
                <div className="rail">
                  <section className="camera">
                    <div className="frame">
                      <div className="guide" />
                      <span className="tag">CAMERA 16:9</span>
                    </div>
                  </section>
                  <section className="mic">
                    <span>음량</span>
                  </section>
                  <section className="next">
                    <button
                      type="button"
                      className="thumb"
                      onClick={() => setSlide((n) => Math.min(PAGES, n + 1))}
                      disabled={onLastSlide}
                    >
                      {onLastSlide ? '마지막 장' : `SLIDE ${slide + 1} · 16:9`}
                    </button>
                    <div className="label">
                      <b>다음 슬라이드</b>
                      <span>→ 키로 이동</span>
                    </div>
                  </section>
                  <div className="legend">
                    <span>테두리 =</span>
                    <i className="audience">청중</i>
                    <i className="screen">화면</i>
                    <i className="unknown">판정 불가</i>
                  </div>
                </div>
              </div>
              {/* 실전 모드는 서버가 코치 말을 보내지 않아 비어 있다 */}
              <div className="coach" data-empty={exam} data-tone="correct">
                <span className="coach-text" role="status">
                  {exam ? '' : '한 호흡 쉬고 가세요.'}
                </span>
              </div>
              {/* 대본 칸(제품 ScriptPane). 실전 모드는 그리지 않는다 */}
              {!exam && (
                <div className="script">
                  {keyword ? (
                    <div className="keywords">
                      <span>해결할 문제</span>
                      <span>37%</span>
                    </div>
                  ) : (
                    <p data-current>
                      지금부터 저희가 <mark>해결하고자 하는 문제</mark>를 말씀드리겠습니다.
                    </p>
                  )}
                  <div className="foot">
                    <span>↑↓ 대본 · ←→ 슬라이드 · 지금 슬라이드의 대본</span>
                  </div>
                </div>
              )}
              <div className="stage-foot">
                <span className="key-help">
                  ← → 슬라이드 이동 · Space 다음 장{!exam && ' · ↑ ↓ 대본 스크롤'}
                </span>
                <div className="stage-status">
                  {stateId === 'disconnected' && (
                    <span className="stt" data-alert="true">
                      말하기 분석 연결이 끊겼어요. 발표는 계속하세요.
                    </span>
                  )}
                  {exam && <span>실전 모드 — 발표 중에는 코치가 말하지 않습니다</span>}
                </div>
                {/* 끝내기는 마지막 장에서만 연다(제품 #697). 제품은 4초 뒤 확인이 풀린다 */}
                <button
                  type="button"
                  className="end"
                  disabled={!onLastSlide || Boolean(gate)}
                  title={onLastSlide ? undefined : '마지막 슬라이드에서 끝낼 수 있어요'}
                  onClick={() =>
                    confirming
                      ? onNavigate('processing-loading')
                      : onNavigate('stage', confirmState)
                  }
                >
                  {confirming ? '정말 끝낼까요?' : '발표 끝내기'}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
