// Draft #1104·#1110의 새미나이 UI. API와 저장소는 사용하지 않는다.
import { useEffect, useId, useRef, useState } from 'react';
import { publicUrl } from '@/lib/publicUrl';
import type { Navigate } from './shared';

const QUESTIONS = [
  '최근 연습에서 개선할 점을 알려줘',
  '이전 연습과 달라진 점을 비교해줘',
  '다음 발표에서 무엇을 연습하면 좋을까?',
] as const;

const SEMINI_IMAGE = publicUrl('semini.png');
const HEADER_ACTION =
  'grid h-11 w-10 shrink-0 place-items-center rounded-lg text-ink hover:bg-cream focus-visible:outline-2 focus-visible:outline-coral';

type PanelView = 'messages' | 'history' | 'memory' | 'closed';

function initialView(stateId: string): PanelView {
  if (stateId === 'closed' || stateId === 'history' || stateId === 'memory') return stateId;
  return 'messages';
}

export function SeminiScreen({ stateId }: { stateId: string; onNavigate: Navigate }) {
  // 화면 선택기에서 프리셋을 바꾸면 새 패널을 마운트해 독립된 상태를 초기화한다.
  return <SeminiPanel key={stateId} stateId={stateId} />;
}

function SeminiPanel({ stateId }: { stateId: string }) {
  const [draft, setDraft] = useState('');
  const [scopeOpen, setScopeOpen] = useState(false);
  const [view, setView] = useState<PanelView>(() => initialView(stateId));
  const [scope, setScope] = useState<'all' | 'pitch'>(() =>
    stateId === 'pitch-scope' ? 'pitch' : 'all',
  );
  const [memoryOff, setMemoryOff] = useState(stateId === 'memory-off');
  const scopeRef = useRef<HTMLButtonElement>(null);
  const launcherRef = useRef<HTMLButtonElement>(null);
  const inputRef = useRef<HTMLTextAreaElement>(null);
  const scopeMenuId = useId();
  const panelId = useId();

  const isClosed = view === 'closed';
  const isHistory = view === 'history';
  const isMemory = view === 'memory';
  const viewChat = !isHistory && !isMemory;
  const wasClosed = useRef(isClosed);
  useEffect(() => {
    if (wasClosed.current && !isClosed) inputRef.current?.focus();
    wasClosed.current = isClosed;
  }, [isClosed]);

  const closePanel = () => {
    setView('closed');
    setScopeOpen(false);
    launcherRef.current?.focus();
  };

  return (
    <div className="relative min-h-[42rem] bg-greige p-8 text-ink">
      <h1 className="text-2xl font-bold">발표 연습 화면</h1>
      <p className="mt-3 text-sm">왼쪽 아래에서 새미나이 채팅을 열어 보세요.</p>
      <div className="absolute bottom-8 left-4 z-10">
        {!isClosed && (
          <section
            id={panelId}
            role="dialog"
            aria-modal="false"
            aria-label="새미나이 채팅 패널"
            className="absolute bottom-full left-0 mb-3 flex h-[36rem] max-h-[calc(100dvh-9rem)] w-[min(26rem,calc(100vw-2rem))] flex-col overflow-hidden rounded-2xl border-[3px] border-ink bg-white shadow-[6px_6px_0_var(--color-ink),0_18px_36px_rgba(42,39,35,0.2)]"
          >
            <header className="shrink-0 border-b border-line-strong bg-panel px-4 pb-3 pt-3">
              <div className="flex items-center justify-between gap-2">
                <div className="flex min-w-0 items-center gap-2.5">
                  <div className="h-10 w-10 shrink-0 overflow-hidden rounded-xl border border-line-strong bg-cream">
                    <img
                      src={SEMINI_IMAGE}
                      alt=""
                      className="h-full w-full object-cover [image-rendering:pixelated]"
                    />
                  </div>
                  <div className="min-w-0">
                    <h2 className="truncate text-base font-bold leading-5">새미나이</h2>
                    <p className="truncate text-[11px] text-stone">Semini · AI 비서 앱</p>
                  </div>
                </div>
                <div className="flex shrink-0 items-center gap-0.5">
                  <button
                    type="button"
                    className={HEADER_ACTION}
                    aria-label="새 대화"
                    onClick={() => {
                      setDraft('');
                      setScopeOpen(false);
                      setView('messages');
                    }}
                  >
                    ＋
                  </button>
                  <button
                    type="button"
                    className={HEADER_ACTION}
                    aria-label="대화 목록"
                    aria-pressed={isHistory}
                    onClick={() => {
                      setView('history');
                      setScopeOpen(false);
                    }}
                  >
                    ◷
                  </button>
                  <button
                    type="button"
                    className={HEADER_ACTION}
                    aria-label="기억 설정"
                    aria-pressed={isMemory}
                    onClick={() => {
                      setView('memory');
                      setScopeOpen(false);
                    }}
                  >
                    ⚙
                  </button>
                  <button
                    type="button"
                    className={HEADER_ACTION}
                    aria-label="닫기"
                    onClick={closePanel}
                  >
                    ×
                  </button>
                </div>
              </div>
              <div className="mt-2 flex min-h-9 flex-wrap items-center justify-between gap-2 text-xs">
                <div className="relative min-w-0">
                  <button
                    ref={scopeRef}
                    type="button"
                    data-chat-scope=""
                    aria-expanded={scopeOpen}
                    aria-controls={scopeOpen ? scopeMenuId : undefined}
                    onClick={() => setScopeOpen((open) => !open)}
                    className="min-h-8 max-w-56 truncate rounded-full border border-line-strong bg-white px-3 text-xs font-bold hover:bg-cream"
                  >
                    <span className="sr-only">범위: </span>
                    {scope === 'pitch' ? '피치: 서비스 소개' : '전체'}
                  </button>
                  {scopeOpen && (
                    <div
                      id={scopeMenuId}
                      role="group"
                      aria-label="범위 고르기"
                      onKeyDown={(event) => {
                        if (event.key === 'Escape') {
                          event.stopPropagation();
                          setScopeOpen(false);
                          scopeRef.current?.focus();
                        }
                      }}
                      className="absolute left-0 top-full z-20 mt-1 max-h-60 w-64 overflow-y-auto rounded-lg border-2 border-ink bg-white p-1 shadow-[3px_3px_0_var(--color-ink)]"
                    >
                      {[
                        { value: 'all', label: '전체' },
                        { value: 'pitch', label: '피치: 서비스 소개' },
                      ].map((option) => (
                        <button
                          key={option.value}
                          type="button"
                          aria-pressed={scope === option.value}
                          onClick={() => {
                            setScopeOpen(false);
                            setScope(option.value === 'pitch' ? 'pitch' : 'all');
                            scopeRef.current?.focus();
                          }}
                          className="block min-h-11 w-full rounded px-2 text-left text-sm hover:bg-cream aria-pressed:font-bold"
                        >
                          {option.label}
                        </button>
                      ))}
                    </div>
                  )}
                </div>
                {viewChat && (
                  <label className="flex items-center gap-1">
                    <input
                      type="checkbox"
                      checked={memoryOff}
                      onChange={(event) => setMemoryOff(event.target.checked)}
                    />
                    기억 없이 대화하기
                  </label>
                )}
              </div>
            </header>
            <div className="flex min-h-16 flex-1 flex-col gap-4 overflow-y-auto bg-white px-4 py-5">
              {viewChat ? (
                <div className="flex flex-1 flex-col items-center justify-center py-8 text-center">
                  <div className="mb-3 h-16 w-16 overflow-hidden rounded-2xl border border-line-strong bg-cream">
                    <img
                      src={SEMINI_IMAGE}
                      alt=""
                      className="h-full w-full object-cover [image-rendering:pixelated]"
                    />
                  </div>
                  <p className="text-base font-bold">새미나이에게 물어보세요</p>
                  <p className="mt-1 text-xs leading-5 text-stone">
                    발표 기록을 분석하거나 다음 연습을 준비할 수 있어요.
                  </p>
                  <div className="mt-5 flex w-full max-w-80 flex-col gap-2" aria-label="추천 질문">
                    {QUESTIONS.map((question) => (
                      <button
                        key={question}
                        type="button"
                        onClick={() => setDraft(question)}
                        className="min-h-11 rounded-xl border border-line-strong bg-white px-3 py-2.5 text-left text-xs leading-5 text-ink transition-colors hover:border-coral hover:bg-coral-wash"
                      >
                        <span aria-hidden="true" className="mr-2 font-bold text-coral-deep">
                          ↗
                        </span>
                        {question}
                      </button>
                    ))}
                  </div>
                </div>
              ) : (
                <div>
                  <h3 className="font-bold">{isHistory ? '대화 목록' : '기억 설정'}</h3>
                  <p className="mt-3 text-sm">
                    설계서에서는 실제 대화 기록과 기억 설정을 저장하지 않아요.
                  </p>
                  <button
                    type="button"
                    onClick={() => setView('messages')}
                    className="mt-4 underline"
                  >
                    대화로 돌아가기
                  </button>
                </div>
              )}
            </div>
            {viewChat && (
              <div className="shrink-0 border-t border-line-strong bg-panel p-3">
                <div className="flex items-end gap-2 rounded-xl border-2 border-line-strong bg-white p-2">
                  <textarea
                    ref={inputRef}
                    aria-label="새미나이에게 메시지 보내기"
                    rows={2}
                    value={draft}
                    onChange={(event) => setDraft(event.target.value)}
                    placeholder="새미나이에게 메시지 보내기..."
                    className="max-h-28 min-h-10 flex-1 resize-none p-2 text-sm"
                  />
                  <button
                    type="button"
                    aria-label="전송"
                    disabled
                    title="설계서 미리보기에서는 실제 메시지를 전송하지 않습니다."
                    className="rounded-xl bg-coral-deep px-3 py-2 text-white opacity-50"
                  >
                    ↑
                  </button>
                </div>
              </div>
            )}
          </section>
        )}
        <button
          ref={launcherRef}
          type="button"
          onClick={() => {
            if (isClosed) setView('messages');
            else closePanel();
          }}
          className="relative block h-11 w-11 overflow-hidden rounded-full border-2 border-ink bg-coral-deep shadow-[3px_3px_0_var(--color-ink)]"
          aria-label="채팅 에이전트"
          aria-expanded={!isClosed}
          aria-haspopup="dialog"
          aria-controls={isClosed ? undefined : panelId}
        >
          <img
            src={SEMINI_IMAGE}
            alt=""
            className="h-full w-full object-cover [image-rendering:pixelated]"
          />
        </button>
      </div>
    </div>
  );
}
