// PM 검토용 독립 시안. 모든 자료와 저장·매핑 결과는 메모리의 예시 데이터다.
import { useEffect, useId, useRef, useState, type ReactNode } from 'react';
import type { Navigate } from './shared';
import './create-concept.css';

const TITLES = ['발표 연습을 더 편하게', '말하기와 시선을 함께', '연습마다 달라지는 나'];
const SCRIPT = [
  '안녕하세요. 오늘은 피치코치를 소개합니다.',
  '발표 자료와 대본을 연결하고 말하기와 시선을 함께 연습해요.',
  '연습을 마치고 지난 Take와 달라진 점을 확인해요.',
];
const CRITERIA = [
  '핵심 주장을 먼저 말하기',
  '근거와 출처를 함께 설명하기',
  '마무리에서 핵심 내용을 요약하기',
];
const GROUPS = ['슬라이드', '대본', '평가기준'] as const;
type Kind = (typeof GROUPS)[number];
type Preview = { kind: Kind | '매핑' | '슬라이드 확인' | '최종 확인'; version: number };
const sections = ['슬라이드', '대본 연결', '발표정보', '평가기준'];

function Icon({ kind }: { kind: 'folder' | 'caret' | 'doc' | 'check' }) {
  const paths = {
    folder: 'M1 4h5l2 2h7v8H1z',
    caret: 'M3 6l5 5 5-5',
    doc: 'M3 1h7l3 3v11H3zM6 7h4M6 10h4',
    check: 'M2 8l4 4 8-8',
  };
  return (
    <svg
      aria-hidden="true"
      focusable="false"
      viewBox="0 0 16 16"
      width="16"
      height="16"
      shapeRendering="crispEdges"
    >
      <path d={paths[kind]} fill="none" stroke="currentColor" strokeWidth="1.5" />
    </svg>
  );
}
function Heading({ children, subtitle }: { children: ReactNode; subtitle?: string }) {
  return (
    <div className="create-pane-heading mb-5">
      <h2 className="text-xl font-bold">{children}</h2>
      {subtitle && <p className="mt-1.5 text-sm text-ink/80">{subtitle}</p>}
    </div>
  );
}
function SampleSlide({ page, version }: { page: number; version: number }) {
  return (
    <div className="create-concept-slide">
      <small>
        예시 슬라이드 · V{version} · {page + 1}/3
      </small>
      <h3>{TITLES[page]}</h3>
      <p>{SCRIPT[page]}</p>
      <span>PITCH COACH</span>
    </div>
  );
}

function SlideMenu({ value, onChange }: { value: number; onChange: (value: number) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  const trigger = useRef<HTMLButtonElement>(null);
  const menuId = useId();
  useEffect(() => {
    if (!open) return;
    ref.current?.querySelector<HTMLButtonElement>('[aria-checked="true"]')?.focus();
    const dismiss = (event: PointerEvent) => {
      if (!ref.current?.contains(event.target as Node)) setOpen(false);
    };
    document.addEventListener('pointerdown', dismiss);
    return () => document.removeEventListener('pointerdown', dismiss);
  }, [open]);
  return (
    <div
      ref={ref}
      className="relative"
      onBlur={(e) => {
        if (!e.currentTarget.contains(e.relatedTarget)) setOpen(false);
      }}
      onKeyDown={(e) => {
        if (e.key === 'Escape') {
          e.preventDefault();
          setOpen(false);
          trigger.current?.focus();
        }
      }}
    >
      <button
        ref={trigger}
        type="button"
        className="create-secondary"
        aria-haspopup="menu"
        aria-expanded={open}
        aria-controls={open ? menuId : undefined}
        onClick={() => setOpen(!open)}
        onKeyDown={(e) => {
          if (e.key === 'ArrowDown') {
            e.preventDefault();
            setOpen(true);
          }
        }}
      >
        연결할 슬라이드 <span>V{value} · 3장</span>
        <Icon kind="caret" />
      </button>
      {open && (
        <div
          id={menuId}
          role="menu"
          aria-label="연결할 슬라이드"
          className="absolute left-0 top-full z-30 mt-2 w-full border border-line-strong bg-white p-2"
          onKeyDown={(e) => {
            const options = [...e.currentTarget.querySelectorAll<HTMLButtonElement>('button')];
            const current = options.indexOf(document.activeElement as HTMLButtonElement);
            let next = current;
            if (e.key === 'ArrowDown') next = (current + 1) % options.length;
            else if (e.key === 'ArrowUp') next = (current - 1 + options.length) % options.length;
            else if (e.key === 'Home') next = 0;
            else if (e.key === 'End') next = options.length - 1;
            else return;
            e.preventDefault();
            options[next]?.focus();
          }}
        >
          {[1, 2, 3].map((v) => (
            <button
              key={v}
              type="button"
              role="menuitemradio"
              aria-checked={value === v}
              tabIndex={-1}
              className="flex min-h-11 w-full items-center gap-3 px-3 text-sm hover:bg-panel focus-visible:bg-panel aria-checked:bg-coral-wash aria-checked:font-bold"
              onClick={() => {
                onChange(v);
                setOpen(false);
                trigger.current?.focus();
              }}
            >
              <span className="w-4">{value === v && <Icon kind="check" />}</span>V{v}
              <span className="ml-auto">3장</span>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}

function PreviewDialog({
  preview,
  inline,
  blocks,
  saved,
  mapping,
  edited,
  onEdit,
  onMap,
  onSave,
  onClose,
  children,
}: {
  preview: Preview;
  inline: boolean;
  blocks: string[];
  saved: boolean;
  mapping: boolean;
  edited: boolean;
  onEdit: (page: number, text: string) => void;
  onMap: () => void;
  onSave: () => void;
  onClose: () => void;
  children: ReactNode;
}) {
  const ref = useRef<HTMLDialogElement>(null);
  const titleId = useId();
  const [page, setPage] = useState(0);
  const isMapping = preview.kind === '매핑';
  const isSlideReview = preview.kind === '슬라이드 확인';
  const isFinalReview = preview.kind === '최종 확인';
  useEffect(() => {
    if (inline) return;
    const previous = document.activeElement as HTMLElement | null;
    const overflow = document.body.style.overflow;
    ref.current?.showModal();
    document.body.style.overflow = 'hidden';
    return () => {
      document.body.style.overflow = overflow;
      previous?.focus({ preventScroll: true });
    };
  }, [inline]);
  let title = `${preview.kind} V${preview.version} · 자료 보기`;
  if (isMapping) title = '매핑 확인·수정';
  if (isSlideReview) title = '슬라이드 확인';
  if (isFinalReview) title = '발표 준비 최종 확인';
  return (
    <dialog
      ref={ref}
      open={inline || undefined}
      aria-labelledby={titleId}
      onClose={onClose}
      className={`create-mapping-dialog ${isFinalReview ? 'create-final-review' : ''} ${inline ? '!static' : ''}`}
      onKeyDown={(e) => {
        if (e.key !== 'Tab') return;
        const items = [
          ...e.currentTarget.querySelectorAll<HTMLElement>(
            'button:not(:disabled), textarea:not(:disabled)',
          ),
        ];
        const first = items[0],
          last = items.at(-1);
        if (e.shiftKey && document.activeElement === first) {
          e.preventDefault();
          last?.focus();
        }
        if (!e.shiftKey && document.activeElement === last) {
          e.preventDefault();
          first?.focus();
        }
      }}
    >
      <header>
        <h2 id={titleId}>{title}</h2>
        <button
          type="button"
          className="create-secondary"
          onClick={() => (inline ? onClose() : ref.current?.close())}
        >
          닫기
        </button>
      </header>
      {!isMapping && !isSlideReview && !isFinalReview && (
        <p className="text-sm">
          확인용 창이에요. 현재 작성 중인 자료와 연습할 버전은 바뀌지 않아요.
        </p>
      )}
      {isSlideReview && <p>올린 PDF의 내용과 순서를 확인한 뒤 저장해 주세요.</p>}
      {(isMapping || isSlideReview || preview.kind === '슬라이드') && (
        <>
          <p className="text-sm">
            슬라이드 V{preview.version} · 3장{isMapping && ' / 대본 V1'}
          </p>
          <div className="create-mapping-workspace">
            <nav aria-label={isMapping ? '매핑 슬라이드 목록' : '슬라이드 목록'}>
              {TITLES.map((_, i) => (
                <button
                  key={i}
                  type="button"
                  aria-current={page === i ? 'true' : undefined}
                  onClick={() => setPage(i)}
                >
                  {i + 1}장{isMapping && ' · 연결됨'}
                </button>
              ))}
            </nav>
            <div className={`create-mapping-selected ${isMapping ? '' : 'create-preview-only'}`}>
              <SampleSlide page={page} version={preview.version} />
              {isMapping && (
                <label className="flex min-h-0 flex-1 flex-col gap-2">
                  <span className="text-sm font-bold">{page + 1}장 대본</span>
                  <textarea
                    aria-label={`슬라이드 ${page + 1} 대본`}
                    disabled={mapping}
                    value={blocks[page]}
                    onChange={(e) => onEdit(page, e.target.value)}
                  />
                </label>
              )}
            </div>
          </div>
          <div className="flex items-center justify-end gap-3">
            <button
              type="button"
              className="create-secondary"
              disabled={page === 0}
              onClick={() => setPage(page - 1)}
            >
              이전 장
            </button>
            <span>{page + 1} / 3장</span>
            <button
              type="button"
              className="create-secondary"
              disabled={page === 2}
              onClick={() => setPage(page + 1)}
            >
              다음 장
            </button>
          </div>
          {isSlideReview && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <p role="status">{saved ? '확인 완료' : '확인 필요'} · 서비스 소개.pdf</p>
              <button type="button" className="create-primary" disabled={saved} onClick={onSave}>
                {saved ? '확인 완료' : '확인하고 저장'}
              </button>
            </div>
          )}
          {isMapping && (
            <div className="flex flex-wrap items-center justify-between gap-3 border-t border-line pt-4">
              <p role="status">
                {saved ? '저장 완료' : '저장 필요'} · 슬라이드 V{preview.version} / 대본 V1
              </p>
              <div className="flex gap-3">
                {edited && (
                  <button
                    type="button"
                    className="create-secondary"
                    disabled={mapping}
                    onClick={onMap}
                  >
                    {mapping ? '매핑 중…' : '수정 대본 매핑'}
                  </button>
                )}
                <button
                  type="button"
                  className="create-primary"
                  disabled={saved || edited || mapping || blocks.some((text) => !text.trim())}
                  onClick={onSave}
                >
                  저장
                </button>
              </div>
            </div>
          )}
        </>
      )}
      {isFinalReview && children}
      {preview.kind === '대본' && (
        <div className="min-h-0 flex-1 overflow-auto bg-white p-5">
          {SCRIPT.map((text, i) => (
            <section key={i} className="mb-6">
              <h3 className="font-bold">
                {i + 1}장 대본 · V{preview.version}
              </h3>
              <p className="mt-2">{text}</p>
            </section>
          ))}
        </div>
      )}
      {preview.kind === '평가기준' && (
        <ol className="min-h-0 flex-1 list-decimal space-y-4 overflow-auto bg-white p-6 pl-10">
          {CRITERIA.map((text) => (
            <li key={text}>{text}</li>
          ))}
        </ol>
      )}
    </dialog>
  );
}

export function CreateConceptScreen({
  stateId,
  onNavigate,
  inline,
}: {
  stateId: string;
  onNavigate: Navigate;
  inline: boolean;
}) {
  const focusOutline = ['focus-outline', 'upload-review', 'final-review'].includes(stateId);
  const ready = ['ready', 'many', 'focus-outline', 'final-review'].includes(stateId);
  const [hasSlides, setHasSlides] = useState(!['empty', 'error'].includes(stateId));
  const [slidesConfirmed, setSlidesConfirmed] = useState(
    !['empty', 'error', 'upload-review'].includes(stateId),
  );
  const [uploadError, setUploadError] = useState(stateId === 'error');
  const [active, setActive] = useState(focusOutline && stateId !== 'upload-review' ? 2 : 0);
  const [linked, setLinked] = useState(1);
  const [mapped, setMapped] = useState(ready || stateId === 'mapping');
  const [saved, setSaved] = useState(ready);
  const [blocks, setBlocks] = useState([...SCRIPT]);
  const [source, setSource] = useState(
    SCRIPT.map((text, i) => `[슬라이드 ${i + 1}]\n${text}`).join('\n\n'),
  );
  const [edited, setEdited] = useState(false);
  const [mapping, setMapping] = useState(false);
  const [mappingError, setMappingError] = useState(stateId === 'mapping-error');
  const [confirmed, setConfirmed] = useState(ready);
  const [title, setTitle] = useState(hasSlides ? '서비스 소개' : '');
  const [minutes, setMinutes] = useState(5);
  const [lower, setLower] = useState(0);
  const [upper, setUpper] = useState(0);
  const [criteriaText, setCriteriaText] = useState(ready ? CRITERIA.join('\n') : '');
  const [criteria, setCriteria] = useState<string[]>(ready ? [...CRITERIA] : []);
  const [criteriaSaved, setCriteriaSaved] = useState(ready);
  const [preview, setPreview] = useState<Preview | null>(() => {
    if (stateId === 'mapping') return { kind: '매핑', version: 1 };
    if (stateId === 'upload-review') return { kind: '슬라이드 확인', version: 1 };
    if (stateId === 'final-review') return { kind: '최종 확인', version: 1 };
    return null;
  });
  const id = useId();
  useEffect(() => {
    if (!mapping) return;
    // 시안에서 완료 전후만 재현한다. 실제 요청·폴링·AI 분석은 하지 않는다.
    const timer = window.setTimeout(() => {
      setMapping(false);
      setMappingError(false);
      setMapped(true);
      setEdited(false);
      setPreview({ kind: '매핑', version: linked });
    }, 500);
    return () => window.clearTimeout(timer);
  }, [mapping, linked]);
  const go = (index: number) => {
    setActive(index);
    document.getElementById(`${id}-${index}`)?.scrollIntoView({ block: 'start' });
  };
  const chooseFile = () => {
    setHasSlides(true);
    setUploadError(false);
    setSlidesConfirmed(false);
    setPreview({ kind: '슬라이드 확인', version: 1 });
    if (!title) setTitle('서비스 소개');
  };
  const complete = Number(slidesConfirmed) + Number(saved) + Number(confirmed);
  let next: number | null = null;
  if (!slidesConfirmed) next = 0;
  else if (!saved) next = 1;
  else if (!confirmed) next = 2;
  else if (criteriaText.trim() && !criteriaSaved) next = 3;
  const versions = Array.from({ length: stateId === 'many' ? 30 : 3 }, (_, i) => i + 1);
  return (
    <div
      className={`pitch-create create-page create-concept${focusOutline ? ' create-focus' : ''}`}
    >
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
                {GROUPS.map((kind) => (
                  <li key={kind}>
                    <details>
                      <summary>
                        <Icon kind="caret" />
                        <Icon kind="folder" />
                        {kind}
                      </summary>
                      <ul>
                        {(kind === '슬라이드' && hasSlides) ||
                        (kind === '대본' && mapped) ||
                        (kind === '평가기준' && criteriaSaved && criteria.length > 0) ? (
                          versions.map((v) => (
                            <li key={v}>
                              <button
                                type="button"
                                aria-label={`${kind} V${v} 보기`}
                                aria-haspopup="dialog"
                                onClick={() => setPreview({ kind, version: v })}
                              >
                                <Icon kind="doc" />V{v}
                              </button>
                            </li>
                          ))
                        ) : (
                          <li className="py-2 text-xs">저장된 버전이 없어요.</li>
                        )}
                      </ul>
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
                필수 준비 <strong>{complete}/3</strong> 완료
              </p>
              <progress aria-label="필수 준비" max={3} value={complete} />
            </div>
          </div>
          <section
            id={`${id}-0`}
            aria-label="슬라이드"
            data-current={focusOutline && active === 0 ? true : undefined}
            onFocusCapture={() => {
              if (focusOutline) setActive(0);
            }}
            onPointerDown={() => {
              if (focusOutline) setActive(0);
            }}
          >
            <Heading subtitle="발표에 사용할 PDF를 올려 주세요.">1. 슬라이드</Heading>
            {hasSlides ? (
              <>
                <div className="create-file-summary">
                  <Icon kind="doc" />
                  <div>
                    <p>서비스 소개.pdf</p>
                    <span>V1 · 3장 · PDF</span>
                  </div>
                </div>
                <div className="mt-4 flex flex-wrap items-center justify-between gap-3">
                  <p role="status">{slidesConfirmed ? '확인 완료' : '업로드 완료 · 확인 필요'}</p>
                  <button
                    type="button"
                    className="create-secondary"
                    onClick={() => setPreview({ kind: '슬라이드 확인', version: 1 })}
                  >
                    슬라이드 확인
                  </button>
                </div>
                <details className="create-slide-preview">
                  <summary>슬라이드 미리보기</summary>
                  <SampleSlide page={0} version={1} />
                  <button
                    type="button"
                    className="create-secondary"
                    onClick={() => setPreview({ kind: '슬라이드', version: 1 })}
                  >
                    크게 보기
                  </button>
                </details>
              </>
            ) : (
              <div className="border-2 border-dashed border-line-strong bg-white p-8 text-center">
                <p className="text-lg font-bold">PDF를 여기에 끌어다 놓으세요</p>
                <p className="my-3 text-sm">파일 이름으로 제목을 채워 드려요 · PDF · 최대 40MB</p>
                <button type="button" className="create-primary" onClick={chooseFile}>
                  PDF 파일 선택
                </button>
                {uploadError && (
                  <div role="alert" className="mt-4">
                    <p>업로드하지 못했어요. 네트워크를 확인하고 다시 시도해 주세요.</p>
                    <button type="button" className="create-secondary mt-3" onClick={chooseFile}>
                      다시 시도
                    </button>
                  </div>
                )}
              </div>
            )}
          </section>
          <section
            id={`${id}-1`}
            aria-label="대본 연결"
            data-current={focusOutline && active === 1 ? true : undefined}
            onFocusCapture={() => {
              if (focusOutline) setActive(1);
            }}
            onPointerDown={() => {
              if (focusOutline) setActive(1);
            }}
          >
            <Heading subtitle="선택한 슬라이드에 맞춰 대본을 준비해 주세요.">2. 대본 연결</Heading>
            {!hasSlides && <p>슬라이드를 먼저 올려 주세요.</p>}
            {hasSlides && !mapped && (
              <>
                <div className="mb-4 flex flex-wrap items-center justify-between gap-3">
                  <SlideMenu value={linked} onChange={setLinked} />
                  <button
                    type="button"
                    className="create-primary"
                    disabled={mapping || !source.trim()}
                    onClick={() => setMapping(true)}
                  >
                    {mapping ? '나누는 중…' : '대본 매핑'}
                  </button>
                </div>
                <textarea
                  aria-label="대본"
                  className="min-h-60 w-full border border-line-strong bg-white p-4"
                  disabled={mapping}
                  value={source}
                  onChange={(e) => setSource(e.target.value)}
                />
                <p className="mt-3 text-sm">슬라이드마다 구분자를 새 줄에 적어 주세요.</p>
                {mappingError && (
                  <p role="alert" className="mt-3">
                    대본을 매핑하지 못했어요. 입력한 대본은 그대로예요. 다시 매핑해 주세요.
                  </p>
                )}
              </>
            )}
            {hasSlides && mapped && (
              <div className="create-mapping-summary">
                <div>
                  <p className="font-bold">
                    슬라이드 V{linked} · 3장 / 대본 V1 · {blocks.length}장
                  </p>
                  <p role="status" className="mt-2 text-sm">
                    {saved ? '저장 완료' : '저장 필요'}
                  </p>
                </div>
                <button
                  type="button"
                  className="create-secondary"
                  onClick={() => setPreview({ kind: '매핑', version: linked })}
                >
                  매핑 확인/수정
                </button>
              </div>
            )}
          </section>
          <section
            id={`${id}-2`}
            aria-label="발표정보"
            data-current={focusOutline && active === 2 ? true : undefined}
            onFocusCapture={() => {
              if (focusOutline) setActive(2);
            }}
            onPointerDown={() => {
              if (focusOutline) setActive(2);
            }}
          >
            <Heading subtitle="발표 제목과 목표 시간을 확인해 주세요.">3. 발표정보</Heading>
            <label className="mb-5 flex flex-col gap-2 font-bold">
              발표 제목
              <input
                maxLength={50}
                value={title}
                onChange={(e) => {
                  setTitle(e.target.value);
                  setConfirmed(false);
                }}
                className="border border-line-strong bg-white p-3 font-normal"
              />
            </label>
            <div className="grid gap-4 sm:grid-cols-3">
              {[
                {
                  label: '목표 발표시간',
                  value: minutes,
                  set: setMinutes,
                  unit: '분',
                  min: 1,
                  step: 1,
                  max: 30,
                },
                {
                  label: '짧게 허용',
                  value: lower,
                  set: setLower,
                  unit: '초',
                  min: 0,
                  step: 30,
                  max: 300,
                },
                {
                  label: '길게 허용',
                  value: upper,
                  set: setUpper,
                  unit: '초',
                  min: 0,
                  step: 30,
                  max: 300,
                },
              ].map((item) => (
                <label key={item.label} className="flex flex-col gap-2 text-sm font-bold">
                  {item.label}
                  <span className="flex items-center gap-2">
                    <input
                      aria-label={item.label}
                      type="number"
                      min={item.min}
                      max={item.max}
                      step={item.step}
                      value={item.value}
                      className="min-w-0 w-full border border-line-strong bg-white p-3"
                      onChange={(e) => {
                        item.set(Number(e.target.value));
                        setConfirmed(false);
                      }}
                    />
                    {item.unit}
                  </span>
                </label>
              ))}
            </div>
            <button
              type="button"
              className="create-primary mt-5"
              disabled={
                !title.trim() ||
                minutes < 1 ||
                minutes > 30 ||
                lower < 0 ||
                upper < 0 ||
                lower > 300 ||
                upper > 300 ||
                lower >= minutes * 60 ||
                confirmed
              }
              onClick={() => setConfirmed(true)}
            >
              {confirmed ? '저장됨' : '발표정보 저장'}
            </button>
          </section>
          <section
            id={`${id}-3`}
            aria-label="평가기준 (선택)"
            data-current={focusOutline && active === 3 ? true : undefined}
            onFocusCapture={() => {
              if (focusOutline) setActive(3);
            }}
            onPointerDown={() => {
              if (focusOutline) setActive(3);
            }}
          >
            <Heading subtitle="연습할 때 무엇을 볼지 정해요. 건너뛰어도 연습할 수 있어요.">
              4. 평가기준 (선택)
            </Heading>
            <div className="grid gap-5 lg:grid-cols-2">
              <div>
                <label className="mb-3 block font-bold" htmlFor={`${id}-criteria`}>
                  평가기준 작성
                </label>
                <textarea
                  id={`${id}-criteria`}
                  className="min-h-40 w-full border border-line-strong bg-white p-4"
                  value={criteriaText}
                  onChange={(e) => {
                    setCriteriaText(e.target.value);
                    setCriteria([]);
                    setCriteriaSaved(false);
                  }}
                  placeholder="핵심 주장을 먼저 말하고, 근거와 출처를 설명하고 싶어요."
                />
                <button
                  type="button"
                  className="create-primary mt-3"
                  disabled={!criteriaText.trim()}
                  onClick={() => {
                    setCriteria([...CRITERIA]);
                    setCriteriaSaved(false);
                  }}
                >
                  평가기준 정리
                </button>
              </div>
              <div>
                <h3 className="mb-3 font-bold">정리된 평가기준</h3>
                {criteria.length ? (
                  <ol className="list-decimal space-y-3 pl-5">
                    {criteria.map((text) => (
                      <li key={text}>{text}</li>
                    ))}
                  </ol>
                ) : (
                  <p>아직 정리된 평가기준이 없어요.</p>
                )}
                {criteria.length > 0 && (
                  <div className="mt-5">
                    <p role="status" className="mb-3 text-sm">
                      {criteriaSaved ? '확인 완료' : '정리된 내용을 확인한 뒤 저장해 주세요.'}
                    </p>
                    <button
                      type="button"
                      className="create-primary"
                      disabled={criteriaSaved}
                      onClick={() => setCriteriaSaved(true)}
                    >
                      {criteriaSaved ? '확인 완료' : '평가기준 확인하고 저장'}
                    </button>
                  </div>
                )}
              </div>
            </div>
          </section>
        </div>
      </div>
      <footer className="create-action-bar">
        <div>
          <p role="status">
            {next === null
              ? `슬라이드 V${linked} / 대본 V1 · 준비한 내용을 최종 확인해 주세요.`
              : '사용할 자료와 설정을 확인하고 저장해 주세요.'}
          </p>
          <div className="create-actions">
            <button type="button" className="create-secondary" onClick={() => onNavigate('home')}>
              내 피치로
            </button>
            <button
              type="button"
              className="create-primary"
              disabled={mapping}
              onClick={() =>
                next === null ? setPreview({ kind: '최종 확인', version: linked }) : go(next)
              }
            >
              {next === null
                ? '최종 확인 →'
                : [
                    '슬라이드 확인하기 ↓',
                    '대본 연결 확인하기 ↓',
                    '발표정보 확인하기 ↓',
                    '평가기준 확인하기 ↓',
                  ][next]}
            </button>
          </div>
        </div>
      </footer>
      {preview && (
        <PreviewDialog
          key={`${preview.kind}-${preview.version}`}
          preview={preview}
          inline={inline}
          blocks={blocks}
          saved={preview.kind === '슬라이드 확인' ? slidesConfirmed : saved}
          mapping={mapping}
          edited={edited}
          onEdit={(page, text) => {
            setBlocks(blocks.map((block, i) => (i === page ? text : block)));
            setSaved(false);
            setEdited(true);
          }}
          onMap={() => setMapping(true)}
          onSave={() => {
            if (preview.kind === '슬라이드 확인') {
              setSlidesConfirmed(true);
              setPreview(null);
            } else setSaved(true);
          }}
          onClose={() => setPreview(null)}
        >
          <p>이 자료와 설정으로 연습할까요? 수정하려면 창을 닫고 해당 항목을 바꿔 주세요.</p>
          <dl className="grid gap-5 border-y border-line py-5">
            {[
              ['슬라이드', `서비스 소개.pdf · V${linked} · 3장`],
              ['대본', `V1 · ${blocks.length}장 · 저장 완료`],
              ['발표 제목', title],
              ['목표 발표시간', `${minutes}분 · 짧게 ${lower}초 / 길게 ${upper}초 허용`],
              [
                '평가기준',
                criteriaSaved && criteria.length ? criteria.join(' / ') : '사용하지 않음',
              ],
            ].map(([label, value]) => (
              <div key={label} className="grid gap-1 sm:grid-cols-[8rem_1fr] sm:gap-4">
                <dt className="font-bold">{label}</dt>
                <dd className="break-words">{value}</dd>
              </div>
            ))}
          </dl>
          <div className="flex flex-wrap justify-end gap-3">
            <button type="button" className="create-secondary" onClick={() => setPreview(null)}>
              돌아가서 수정
            </button>
            <button
              type="button"
              className="create-primary"
              disabled={next !== null}
              onClick={() => onNavigate('prepare-mission')}
            >
              확인하고 시작 전 점검 →
            </button>
          </div>
        </PreviewDialog>
      )}
    </div>
  );
}
