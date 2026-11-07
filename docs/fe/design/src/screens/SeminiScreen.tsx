// Draft #1104 + #1110의 새미나이 채팅 패널을 API 없이 재현한 디자인 샘플.
import { useState } from 'react';
const SEMINI_IMAGE = 'data:image/png;base64,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
IAgEgSAQBIJAEAgCQSAIBIEgEASCQBAIAkEgCASBIBAEgkAQCAJBIAgEgSAQ
BIJAEAgCAQQQQAABBBBAAAEEEEAAAQQQQAABBBBAAAEEEEAAAQQQQAABBBBO
hwDHthcdBBBAAAEEEEAAAQQQQAABBBBAEAgCQSAIBIEgEASCQBAIAkEgaM0Q
4NjOooMAAggggAACCCCAAAIIIIAAAggggAACCCCAAAIIAkEgCASBIBAEgkAQ
CAJBIAgEgSAQBIJAEAgCQSAIBIEgEEAAAQQQQAABBBBAAAEEEEAA4XQcwJy3
wHMvOggggAACCCCAAAIIIIAAAggggAACCCCAAAIIIIAAAggggAACCCCAAAII
IIAAAgggbAGCC1/PW/Sl1gMEEEAAAQQQQAABBBBAAAEEEEAAAQQQQAABBBBA
AAEEEEAAAQQQQAABBBBAAAEEELYK4Vgca78Y9pwxr2nuQQABBBBAAAEEEEAA
AQQQQAABBBBAAAEEEEAAAQQQQAABBBBAAAEEEEAAAQQQQADhliAshWgriwkC
CCCAAAIIIIAAAggggAACCCCAAAIIIIAAAggggAACCCCAAAIIIIAAAggggAAC
CCvuX00j7H7E4vqzAAAAAElFTkSuQmCC
';

const QUESTIONS = ['발표를 어떻게 시작하면 좋을까?', '내 발표 시간을 줄이는 방법은?', '연습할 때 무엇을 확인해야 할까?'];

export function SeminiScreen() {
  const [open, setOpen] = useState(true);
  const [tab, setTab] = useState<'chat' | 'history' | 'memory'>('chat');
  const [draft, setDraft] = useState('');
  const [scope, setScope] = useState<'all' | 'pitch'>('all');
  const [noMemory, setNoMemory] = useState(false);
  return (
    <div className="relative min-h-[42rem] bg-greige p-8 text-ink">
      <h1 className="text-2xl font-bold">발표 연습 화면</h1>
      <p className="mt-3 text-sm">왼쪽 아래에서 새미나이 채팅을 열어 보세요.</p>
      <div className="absolute bottom-8 left-4 z-10 flex flex-col items-start gap-3">
        {open && (
          <section aria-label="새미나이 채팅 패널" className="flex h-[32rem] w-[min(23rem,calc(100vw-3rem))] flex-col border-[3px] border-ink bg-panel shadow-[6px_6px_0_var(--color-ink),0_14px_32px_rgba(0,0,0,.16)]">
            <header className="border-b border-line-strong bg-cream p-3">
              <div className="flex items-center gap-2">
                <img src={SEMINI_IMAGE} alt="" className="h-10 w-10 object-contain [image-rendering:pixelated]" />
                <strong className="flex-1">새미나이 <span className="text-xs font-normal">(Semini)</span></strong>
                <button type="button" aria-label="새 대화" onClick={() => { setTab('chat'); setDraft(''); }} className="border border-ink px-2 py-1">＋</button>
                <button type="button" aria-label="대화 목록" onClick={() => setTab('history')} className="border border-ink px-2 py-1">☷</button>
                <button type="button" aria-label="기억 설정" onClick={() => setTab('memory')} className="border border-ink px-2 py-1">⚙</button>
                <button type="button" aria-label="채팅 닫기" onClick={() => setOpen(false)} className="border border-ink px-2 py-1">×</button>
              </div>
              {tab === 'chat' && <div className="mt-3 flex flex-wrap items-center gap-3 text-xs">
                <label>대화 범위 <select aria-label="대화 범위" value={scope} onChange={(event) => setScope(event.target.value as 'all' | 'pitch')} className="border border-line-strong bg-white p-1"><option value="all">전체</option><option value="pitch">현재 피치</option></select></label>
                <label className="flex items-center gap-1"><input type="checkbox" checked={noMemory} onChange={(event) => setNoMemory(event.target.checked)} />기억 없이 대화하기</label>
              </div>}
            </header>
            <div className="min-h-0 flex-1 overflow-y-auto p-4">
              {tab === 'chat' ? (
                <div className="space-y-4 text-center">
                  <img src={SEMINI_IMAGE} alt="새미나이" className="mx-auto h-20 w-20 object-contain [image-rendering:pixelated]" />
                  <h2 className="font-bold">무엇을 도와드릴까요?</h2>
                  <p className="text-xs text-ink/70">발표 준비부터 연습까지 궁금한 점을 물어보세요.</p>
                  {QUESTIONS.map((question) => <button type="button" key={question} onClick={() => setDraft(question)} className="block w-full border border-line-strong bg-white p-3 text-left text-sm hover:bg-coral-wash">{question}</button>)}
                </div>
              ) : <div><h2 className="font-bold">{tab === 'history' ? '대화 목록' : '기억 설정'}</h2><p className="mt-3 text-sm">설계서 미리보기에서는 실제 대화 기록과 설정을 저장하지 않아요.</p><button type="button" className="mt-4 underline" onClick={() => setTab('chat')}>대화로 돌아가기</button></div>}
            </div>
            {tab === 'chat' && <div className="border-t border-line-strong bg-panel p-3">
              <div className="flex items-end gap-2 rounded-xl border-2 border-ink bg-white p-2">
                <textarea aria-label="새미나이에게 질문" rows={2} className="max-h-28 min-h-10 flex-1 resize-none p-2 text-sm" value={draft} onChange={(event) => setDraft(event.target.value)} placeholder="새미나이에게 질문해 보세요" />
                <button type="button" aria-label="전송" disabled={!draft.trim()} className="bg-coral-deep px-3 py-2 text-white disabled:opacity-50">↑</button>
              </div>
            </div>}
          </section>
        )}
        <button type="button" onClick={() => setOpen((value) => !value)} className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-ink bg-coral-deep text-white shadow-[3px_3px_0_var(--color-ink)]" aria-label={open ? '새미나이 닫기' : '새미나이 열기'}><img src={SEMINI_IMAGE} alt="" className="h-10 w-10 object-contain [image-rendering:pixelated]" /></button>
      </div>
    </div>
  );
}
