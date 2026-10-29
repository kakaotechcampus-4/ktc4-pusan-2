// 제품 리허설 무대(`frontend/src/features/rehearsal/stage/RehearsalPage.tsx`, `ScriptPane.tsx`)의 설계서 샘플.
import type { Navigate } from './shared';
import '@/ui/stage.css';

/**
 * 제품 RehearsalPage의 무대 DOM을 같은 클래스로 그린다. 배치 · 색은 src/ui/stage.css가 정한다.
 * 클래스 이름은 ScreenPreview.test가 stage.css에 있는지 확인한다.
 */
export function StageScreen({ stateId, onNavigate }: { stateId: string; onNavigate: Navigate }) {
  const exam = stateId === 'exam' || stateId === 'exam-confirm';
  const keyword = stateId === 'keyword' || stateId === 'keyword-confirm';
  const confirming =
    stateId === 'confirm-end' || stateId === 'exam-confirm' || stateId === 'keyword-confirm';
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
    <div className="flex h-[40rem] flex-col">
      <div className="rehearsal-page">
        <div className="rehearsal-content">
          <h1 className="sr-only">발표 연습 · Take 3</h1>
          <div className="rehearsal-frame">
            <div
              className="stage stage-live"
              data-script-mode={scriptMode}
              data-gaze="AUDIENCE"
              data-mode={exam ? 'EXAM' : 'COACHING'}
            >
              <header className="stage-head">
                <span className="elapsed">02:14</span>
                <span className="limit">/ 05:00</span>
                <div className="bar" data-over="false">
                  <i style={{ width: '45%' }} />
                </div>
                <span className="meta">남은 02:46</span>
                <span className="slide-no">SLIDE 3 / 12</span>
              </header>
              <div className="viewport">
                <div className="slide">
                  <span className="placeholder">SLIDE 3 · 16:9</span>
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
                    <div className="thumb">SLIDE 4 · 16:9</div>
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
                {/* 마지막 장이 아니라 "중간 종료". 제품은 4초 뒤 확인이 풀린다 */}
                <button
                  type="button"
                  className="end"
                  onClick={() =>
                    confirming
                      ? onNavigate('processing-loading')
                      : onNavigate('stage', confirmState)
                  }
                >
                  {confirming ? '정말 끝낼까요?' : '중간 종료'}
                </button>
              </div>
            </div>
          </div>
        </div>
      </div>
    </div>
  );
}
