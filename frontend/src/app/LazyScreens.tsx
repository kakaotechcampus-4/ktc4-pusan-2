import { lazy } from 'react';

// 첫 진입 화면은 즉시 표시하고, PDF·시선 엔진·개발 시안은 해당 화면 진입 때만 가져옵니다.
export const StageDemo = lazy(() =>
  import('@/features/rehearsal/stage/StageDemo').then((m) => ({ default: m.StageDemo })),
);
export const MediaDevPage = lazy(() =>
  import('@/features/rehearsal/media/MediaDevPage').then((m) => ({ default: m.MediaDevPage })),
);
export const SttDevPage = lazy(() =>
  import('@/features/rehearsal/media/SttDevPage').then((m) => ({ default: m.SttDevPage })),
);
export const PolicyPage = lazy(() =>
  import('@/features/onboarding/PolicyPage').then((m) => ({ default: m.PolicyPage })),
);
export const DeviceCheckPage = lazy(() =>
  import('@/features/rehearsal/prepare/DeviceCheckPage').then((m) => ({
    default: m.DeviceCheckPage,
  })),
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
  import('@/features/report/ProcessingPage').then((m) => ({ default: m.ProcessingPage })),
);
export const ReportPage = lazy(() =>
  import('@/features/report/ReportPage').then((m) => ({ default: m.ReportPage })),
);
export const DesignIndex = lazy(() =>
  import('@/dev/design/DesignIndex').then((m) => ({ default: m.DesignIndex })),
);
export const DesignDraftRoute = lazy(() =>
  import('@/dev/design/DesignIndex').then((m) => ({ default: m.DesignDraftRoute })),
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
  import('@/features/report/TakesPage').then((m) => ({ default: m.TakesPage })),
);
export const ComparisonPage = lazy(() =>
  import('@/features/report/ComparisonPage').then((m) => ({ default: m.ComparisonPage })),
);

export const ConfirmPage = lazy(() =>
  import('@/features/report/ConfirmPage').then((m) => ({ default: m.ConfirmPage })),
);
