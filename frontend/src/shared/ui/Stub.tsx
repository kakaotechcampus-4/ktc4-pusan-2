import { Link } from 'react-router';

/** W4용 자리표시. 화면을 실제로 만들 때 이 컴포넌트를 지우고 대체합니다. */
export function Stub({ id, name, track }: { id: string; name: string; track: 'A' | 'B' }) {
  const isA = track === 'A';
  return (
    <div className="min-h-full flex items-center justify-center p-10">
      <div className="max-w-md w-full bg-panel border border-line rounded-2xl p-8 flex flex-col gap-3">
        <div className="flex items-center gap-2">
          <span className="font-mono text-xs font-bold text-coral-deep">{id}</span>
          <span
            className={`text-[10px] font-bold px-2 py-0.5 rounded-full ${
              isA ? 'bg-coral-wash text-coral-deep' : 'bg-cream text-stone'
            }`}
          >
            TRACK {track}
          </span>
        </div>
        <h1 className="text-xl font-bold">{name}</h1>
        <p className="text-sm text-stone leading-relaxed">
          아직 만들지 않은 화면입니다. 인터페이스 명세 8-2에서 이 화면이 부르는 API를 먼저
          확인하세요.
        </p>
        <Link to="/" className="text-sm text-coral-deep font-semibold hover:underline">
          홈으로
        </Link>
      </div>
    </div>
  );
}
