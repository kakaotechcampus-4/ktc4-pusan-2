import { useEffect, type ReactNode } from 'react';

/** 라우트가 바뀔 때 브라우저 탭 제목을 갱신한다. */
export function PageTitle({ title, children }: { title: string; children: ReactNode }) {
  useEffect(() => {
    document.title = title;
  }, [title]);

  return <>{children}</>;
}
