// Draft #1104 + #1110의 새미나이 채팅 패널을 API 없이 재현한 디자인 샘플.
import { useState } from 'react';
import { publicUrl } from '@/lib/publicUrl';

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
                <img src={publicUrl('semini.png')} alt="" className="h-10 w-10 object-contain [image-rendering:pixelated]" />
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
                  <img src={publicUrl('semini.png')} alt="새미나이" className="mx-auto h-20 w-20 object-contain [image-rendering:pixelated]" />
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
        <button type="button" onClick={() => setOpen((value) => !value)} className="flex h-14 w-14 items-center justify-center rounded-full border-2 border-ink bg-coral-deep text-white shadow-[3px_3px_0_var(--color-ink)]" aria-label={open ? '새미나이 닫기' : '새미나이 열기'}><img src={publicUrl('semini.png')} alt="" className="h-10 w-10 object-contain [image-rendering:pixelated]" /></button>
      </div>
    </div>
  );
}
