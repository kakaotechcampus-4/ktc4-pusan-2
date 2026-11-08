import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { RouterProvider } from 'react-router';
import { QueryClient, QueryClientProvider } from '@tanstack/react-query';
import { router } from '@/app/routes';
import { bindChatToSession } from '@/features/chat/chatStore';
import './index.css';

// 대화 에이전트를 로그아웃 신호에 묶습니다 — 화면을 그리기 전에, 경로 · 로그인 조건과 상관없이 한 번.
// 대화창(#941)은 로그인 · 허용 경로에서만 그려져서, 거기서 묶으면 리허설 화면 헤더에서 로그아웃할 때
// 이 탭의 sessionStorage 사본(보내던 질문 원문 포함)이 남습니다. 여기서 묶으면 이 탭은 새로 열기 전에,
// 다른 탭은 채널 신호로 각자의 사본 · 메모리를 비웁니다(`shared/lib/sessionSignal.ts`)
bindChatToSession();

const queryClient = new QueryClient({
  defaultOptions: {
    queries: {
      staleTime: 30_000,
      retry: 1,
      refetchOnWindowFocus: false,
    },
  },
});

async function boot() {
  // 개발 중에는 항상 목 서버 위에서 돕니다.
  // BE 실서버가 준비되면 VITE_USE_MOCK=false 로 끕니다 (W8).
  if (import.meta.env.DEV && import.meta.env.VITE_USE_MOCK !== 'false') {
    const { worker } = await import('@/mocks/browser');
    await worker.start({ onUnhandledRequest: 'bypass' });
  }

  createRoot(document.getElementById('root')!).render(
    <StrictMode>
      <QueryClientProvider client={queryClient}>
        <RouterProvider router={router} />
      </QueryClientProvider>
    </StrictMode>,
  );
}

boot();
