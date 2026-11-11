import { lazy } from 'react';

// 첫 진입 화면은 즉시 표시하고, PDF·시선 엔진은 해당 화면 진입 때만 가져옵니다.
export const StageDemo = lazy(() =>
  import('@/dev/StageDemo').then((m) => ({ default: m.StageDemo })),
);
export const MediaDevPage = lazy(() =>
  import('@/dev/MediaDevPage').then((m) => ({ default: m.MediaDevPage })),
);
export const SttDevPage = lazy(() =>
  import('@/dev/SttDevPage').then((m) => ({ default: m.SttDevPage })),
);
export const PolicyPage = lazy(() =>
  import('@/features/onboarding/PolicyPage').then((m) => ({ default: m.PolicyPage })),
);
export const DeviceCheckPage = lazy(() =>
  import('@/features/rehearsal/prepare/DeviceCheckPage').then((m) => ({
    default: m.DeviceCheckPage,
  })),
);
export const FirstTakePage = lazy(() =>
  import('@/features/pitch/tutorial/FirstTakePage').then((m) => ({ default: m.FirstTakePage })),
);
export const PitchCreatePage = lazy(() =>
  import('@/features/pitch/create/PitchCreatePage').then((m) => ({ default: m.PitchCreatePage })),
);
export const PitchEditPage = lazy(() =>
  import('@/features/pitch/create/PitchCreatePage').then((m) => ({ default: m.PitchEditPage })),
);
export const RehearsalPage = lazy(() =>
  import('@/features/rehearsal/stage/RehearsalPage').then((m) => ({ default: m.RehearsalPage })),
);
export const RetryPage = lazy(() =>
  import('@/features/rehearsal/retry/RetryPage').then((m) => ({ default: m.RetryPage })),
);
export const ProcessingPage = lazy(() =>
  import('@/features/report/analysis/ProcessingPage').then((m) => ({ default: m.ProcessingPage })),
);
export const ReportPage = lazy(() =>
  import('@/features/report/report/ReportPage').then((m) => ({ default: m.ReportPage })),
);
export const AboutPage = lazy(() =>
  import('@/features/info/AboutPage').then((m) => ({ default: m.AboutPage })),
);
export const UnsupportedPage = lazy(() =>
  import('@/features/info/UnsupportedPage').then((m) => ({ default: m.UnsupportedPage })),
);
export const NotFoundPage = lazy(() =>
  import('@/features/info/NotFoundPage').then((m) => ({ default: m.NotFoundPage })),
);
export const MyPage = lazy(() =>
  import('@/features/auth/MyPage').then((m) => ({ default: m.MyPage })),
);
export const TakesPage = lazy(() =>
  import('@/features/report/history/TakesPage').then((m) => ({ default: m.TakesPage })),
);

export const ConfirmPage = lazy(() =>
  import('@/features/report/analysis/ConfirmPage').then((m) => ({ default: m.ConfirmPage })),
);
