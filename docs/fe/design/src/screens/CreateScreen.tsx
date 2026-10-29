// 제품 PitchCreatePage · create.css의 한 페이지 흐름을 그리는 독립 샘플. API·PDF 렌더링은 생략한다.
import { useEffect, useRef, useState } from 'react';
import { Button } from '@/ui/Button';
import { PaneHeading } from '@/ui/PaneHeading';
import { PITCH, type Navigate } from './shared';
import './create.css';

const sections = ['슬라이드', '대본 연결', '발표정보', '평가기준'];
export function CreateScreen({
  stateId,
  onNavigate,
  inline,
}: {
  stateId: string;
  onNavigate: Navigate;
  inline: boolean;
}) {
  const hasSlides = stateId !== 'empty' && stateId !== 'error';
  const [active, setActive] = useState(0);
  const [mapped, setMapped] = useState(false);
  const [confirmed, setConfirmed] = useState(false);
  const [preview, setPreview] = useState<string | null>(
    stateId === 'preview' ? '슬라이드 V1 · 자료 보기' : null,
  );
  const dialogRef = useRef<HTMLDialogElement>(null);
  const returnFocus = useRef<HTMLElement | null>(null);
  useEffect(() => {
    if (!preview || inline) return;
    const dialog = dialogRef.current!;
    returnFocus.current = document.activeElement as HTMLElement;
    dialog.showModal();
    return () => {
      returnFocus.current?.focus({ preventScroll: true });
    };
  }, [preview, inline]);
  const go = (index: number) => {
    setActive(index);
    document.getElementById(`sample-create-${index}`)?.scrollIntoView({ block: 'start' });
  };
  const close = () => {
    setPreview(null);
    if (stateId === 'preview') onNavigate('pitch-create', 'default');
  };
  return (
    <div className="pitch-create create-page">
      <div className="create-layout">
        <aside className="create-sidebar">
          <nav aria-label="준비 순서">
            <h2>준비 순서</h2>
            <ol>
              {sections.map((label, i) => (
                <li key={label}>
                  <button
                    type="button"
                    aria-current={active === i ? 'step' : undefined}
                    onClick={() => go(i)}
                  >
                    <span className="create-step-number">{i + 1}</span>
                    {label}
                    {i === 3 && <small>선택</small>}
                  </button>
                </li>
              ))}
            </ol>
          </nav>
          <details className="create-versions">
            <summary>자료 버전 관리</summary>
            <nav aria-label="자료 버전 보기">
              <ul className="create-version-tree">
                {['슬라이드', '대본', '평가기준'].map((label) => (
                  <li key={label}>
                    <details>
                      <summary>{label}</summary>
                      {(hasSlides && label === '슬라이드') || (mapped && label === '대본') ? (
                        <button type="button" onClick={() => setPreview(`${label} V1 · 자료 보기`)}>
                          V1
                        </button>
                      ) : (
                        <p className="py-2 text-xs">저장된 버전이 없어요.</p>
                      )}
                    </details>
                  </li>
                ))}
              </ul>
            </nav>
          </details>
        </aside>
        <div className="create-content">
          <div className="create-intro">
            <div>
              <h1>발표 준비</h1>
              <p>자료부터 연습 설정까지, 이 페이지에서 준비해요.</p>
            </div>
            <div className="create-progress">
              <p>
                필수 준비{' '}
                <strong>{Number(hasSlides) + Number(mapped) + Number(confirmed)}/3</strong> 완료
              </p>
              <progress
                aria-label="필수 준비"
                max={3}
                value={Number(hasSlides) + Number(mapped) + Number(confirmed)}
              />
            </div>
          </div>
          <section id="sample-create-0" aria-label="슬라이드">
            <PaneHeading title="1. 슬라이드" subtitle="발표에 사용할 PDF를 올려 주세요." />
            {hasSlides ? (
              <>
                <div className="create-file-summary">
                  <div>
                    <p>{PITCH}.pdf</p>
                    <span>V1 · 12장 · PDF</span>
                  </div>
                </div>
                <details className="create-slide-preview">
                  <summary>슬라이드 미리보기</summary>
                  <div className="bg-cream p-8">
                    <p>{PITCH}</p>
                    <Button
                      variant="secondary"
                      onClick={() => onNavigate('pitch-create', 'preview')}
                    >
                      슬라이드 크게 보기
                    </Button>
                  </div>
                </details>
              </>
            ) : (
              <div className="border-2 border-dashed border-line-strong bg-white p-8 text-center">
                <p className="text-lg font-bold">PDF를 여기에 끌어다 놓으세요</p>
                <p className="my-3 text-sm">파일 이름으로 제목을 채워 드려요 · PDF · 최대 40MB</p>
                <p className="mb-4 text-xs">
                  PDF를 올리면 피치가 자동으로 만들어져요. 업로드에 실패하거나 나가도 내 피치에
                  남아, 나중에 이어서 준비할 수 있어요.
                </p>
                <Button onClick={() => onNavigate('pitch-create', 'default')}>PDF 파일 선택</Button>
                {stateId === 'error' && (
                  <p role="alert" className="mt-3">
                    업로드하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.
                  </p>
                )}
              </div>
            )}
          </section>
          <section id="sample-create-1" aria-label="대본 연결">
            <PaneHeading
              title="2. 대본 연결"
              subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요."
            />
            {!hasSlides && <p>슬라이드를 먼저 올려 주세요.</p>}
            {hasSlides && mapped && (
              <div className="create-mapping-summary">
                <p>슬라이드 V1 · 12장 / 대본 V1 · 12장 · 저장 완료</p>
                <Button variant="secondary" onClick={() => setPreview('매핑 확인·수정')}>
                  매핑 확인/수정
                </Button>
              </div>
            )}
            {hasSlides && !mapped && (
              <>
                <p className="mb-3">연결할 슬라이드 V1 · 12장</p>
                <textarea
                  aria-label="대본"
                  className="min-h-48 w-full border border-line-strong p-4"
                  defaultValue={'[슬라이드 1]\n안녕하세요. 오늘 준비한 발표를 시작하겠습니다.'}
                />
                <Button onClick={() => setPreview('매핑 확인·수정')}>대본 매핑</Button>
              </>
            )}
          </section>
          <section id="sample-create-2" aria-label="발표정보">
            <PaneHeading title="3. 발표정보" subtitle="제목과 목표 발표시간을 확인해 주세요." />
            <label className="mb-4 flex flex-col gap-2 font-bold">
              발표 제목
              <input
                className="border border-line-strong p-3"
                defaultValue={hasSlides ? PITCH : ''}
              />
            </label>
            <label className="flex flex-col gap-2 font-bold">
              목표 발표시간
              <input className="border border-line-strong p-3" defaultValue="5분" />
            </label>
            <p className="my-4">짧게 허용 0초 · 길게 허용 0초</p>
            <Button onClick={() => setConfirmed(true)}>
              {confirmed ? '저장됨' : '발표정보 저장'}
            </Button>
          </section>
          <section id="sample-create-3" aria-label="평가기준 (선택)">
            <PaneHeading
              title="4. 평가기준 (선택)"
              subtitle="연습할 때 무엇을 볼지 정해요. 건너뛰어도 연습할 수 있어요."
            />
            <textarea
              aria-label="평가기준 작성"
              className="min-h-32 w-full border border-line-strong p-4"
              placeholder="발표에서 특히 신경 쓸 기준을 적어요."
            />
          </section>
        </div>
      </div>
      <footer className="create-action-bar">
        <div>
          <p>자료와 발표정보를 저장한 뒤 시작 전 점검으로 이동해요.</p>
          <div className="create-actions">
            <Button variant="secondary" onClick={() => onNavigate('home')}>
              내 피치로
            </Button>
            <Button
              disabled={!hasSlides || !mapped || !confirmed}
              onClick={() => onNavigate('prepare-mission')}
            >
              시작 전 점검 →
            </Button>
          </div>
        </div>
      </footer>
      {preview && (
        <dialog
          ref={dialogRef}
          open={inline || undefined}
          onClose={close}
          aria-label={preview}
          className={`create-mapping-dialog ${inline ? '!static' : ''}`}
        >
          <header>
            <h2>{preview}</h2>
            <Button autoFocus={!inline} variant="secondary" onClick={close}>
              닫기
            </Button>
          </header>
          <div className="flex-1 overflow-auto bg-white p-6">
            {preview === '매핑 확인·수정' ? (
              <>
                <p className="mb-4 font-bold">1장 / 12장</p>
                <textarea
                  aria-label="슬라이드 1 대본"
                  className="min-h-48 w-full border border-line p-4"
                  defaultValue="안녕하세요. 오늘 준비한 발표를 시작하겠습니다."
                />
                <Button
                  onClick={() => {
                    setMapped(true);
                    close();
                  }}
                >
                  저장
                </Button>
              </>
            ) : (
              <>
                <p className="text-xl font-bold">{PITCH}</p>
                <p className="mt-4">
                  확인용 창이에요. 현재 작성 중인 자료와 연습할 버전은 바뀌지 않아요.
                </p>
              </>
            )}
          </div>
        </dialog>
      )}
    </div>
  );
}
