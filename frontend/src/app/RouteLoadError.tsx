import { Button, ButtonLink } from '@/shared/ui/Button';

/** 네트워크 단절이나 배포 후 없어진 화면 파일을 다시 요청할 수 있게 합니다. */
export function RouteLoadError() {
  return (
    <main className="min-h-dvh bg-panel p-8 text-ink">
      <div role="alert" className="mx-auto flex max-w-lg flex-col gap-4">
        <h1 className="text-xl font-bold">화면을 불러오지 못했어요.</h1>
        <p>연결을 확인한 뒤 새로고침해 주세요.</p>
        <div className="flex flex-wrap gap-3">
          <Button onClick={() => window.location.reload()}>새로고침</Button>
          <ButtonLink to="/" variant="secondary">
            홈으로
          </ButtonLink>
        </div>
      </div>
    </main>
  );
}
