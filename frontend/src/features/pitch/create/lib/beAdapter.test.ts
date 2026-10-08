import { describe, expect, it } from 'vitest';
import type { UploadPresentationResponse } from '@/types/presentation';
import type { ScriptDetail } from '@/types/script';
import type { StandardsPosted } from '@/types/standards';
import {
  fromPitchSaved,
  fromScriptCreated,
  fromScriptDetail,
  fromStandardsPosted,
  fromUploadedPresentation,
  toPitchRequest,
} from './beAdapter';

/**
 * BE 와의 계약을 고정합니다. 픽스처는 **BE 응답 모양 그대로**입니다
 * (backend/src/pitch_coach_backend/module/pitch — controller.py · dto.py).
 *
 * BE 가 응답을 바꾸면 이 테스트부터 깨지고, 고칠 곳은 `beAdapter.ts` 하나입니다.
 */

describe('피치 — PitchSaveRequestDTO', () => {
  it('하한 · 상한 허용오차를 따로 보낸다', () => {
    expect(
      toPitchRequest({
        title: '  캡스톤 중간발표 ',
        presentationDate: '2026-10-09',
        timeLimitSec: 300,
        lowerToleranceSec: 30,
        upperToleranceSec: 60,
      }),
    ).toEqual({
      title: '캡스톤 중간발표',
      time_limit_sec: 300,
      upper_deviation: 60,
      lower_deviation: 30,
      presentation_date: '2026-10-09',
    });
  });

  it('날짜를 안 골랐으면 null — 빈 문자열은 BE 의 date 로 읽히지 않는다', () => {
    const req = toPitchRequest({
      title: '제목',
      presentationDate: '',
      timeLimitSec: 60,
      lowerToleranceSec: 0,
      upperToleranceSec: 0,
    });
    expect(req.presentation_date).toBeNull();
  });
});

describe('피치 — 저장 응답', () => {
  it('생성 · 수정 모두 pitch_id 를 쓴다', () => {
    expect(fromPitchSaved({ message: 'Pitch added successfully', pitch_id: 'p1' })).toBe('p1');
  });
});

describe('발표자료 — 업로드 응답', () => {
  it('presentation 안에서 꺼낸다', () => {
    const res: UploadPresentationResponse = {
      message: 'Presentation uploaded successfully',
      presentation: {
        pitch_id: 'p1',
        presentation_version_id: 'pv1',
        file_url: 'https://s3.example/p1.pdf',
      },
    };
    expect(fromUploadedPresentation(res)).toEqual({
      fileUrl: 'https://s3.example/p1.pdf',
      presentationVersionId: 'pv1',
    });
  });
});

describe('평가기준 — 컨트롤러가 pitch_id 안에 감싼 결과', () => {
  it('pitch_id 안의 standards 와 except_standard 를 꺼낸다', () => {
    const res: StandardsPosted = {
      message: 'Pitch standard text added successfully',
      pitch_id: {
        pitch_id: 'p1',
        standards: [{ standard: '시장 규모 말하기' }, { standard: '채움말 5회 이하' }],
        except_standard: '발표를 멋지게',
      },
    };
    expect(fromStandardsPosted(res)).toEqual({
      standards: ['시장 규모 말하기', '채움말 5회 이하'],
      exceptText: '발표를 멋지게',
    });
  });

  it('BE 가 아직 나누지 못하면(결과 null) null — 빈 목록으로 보지 않는다', () => {
    expect(fromStandardsPosted({ message: 'ok', pitch_id: null })).toBeNull();
  });
});

describe('대본 — 올린 응답 (202)', () => {
  it('script_version_id 와 서버 버전을 꺼낸다', () => {
    expect(
      fromScriptCreated({ script_version_id: 's1', version: 3, parse_status: 'PENDING' }),
    ).toEqual({ id: 's1', version: 3 });
  });
});

describe('대본 — 폴링 응답', () => {
  const base: ScriptDetail = {
    script_version_id: 's1',
    version: 1,
    original_content: '슬라이드 1\n가\n슬라이드 2\n나',
    parse_status: 'PENDING',
    segmented: null,
    slides: [],
    terms: [],
    error_code: null,
  };

  it('PENDING 이면 기다린다', () => {
    expect(fromScriptDetail(base)).toEqual({ status: 'pending' });
  });

  it('DONE 이면 slide_number 순서로 본문을 꺼낸다 — 원문이 아니라 content', () => {
    const progress = fromScriptDetail({
      ...base,
      parse_status: 'DONE',
      segmented: true,
      slides: [
        { slide_number: 2, content: '나', keywords: [], highlights: [] },
        { slide_number: 1, content: '가', keywords: ['가'], highlights: [{ start: 0, end: 1 }] },
      ],
    });
    expect(progress).toEqual({ status: 'done', blocks: ['가', '나'], segmented: true });
  });

  it('구분자가 없어 한 슬라이드로 둔 것도 DONE 이다 (segmented: false)', () => {
    const progress = fromScriptDetail({
      ...base,
      parse_status: 'DONE',
      segmented: false,
      slides: [{ slide_number: 1, content: '전체', keywords: [], highlights: [] }],
    });
    expect(progress).toEqual({ status: 'done', blocks: ['전체'], segmented: false });
  });

  /** script-parser 는 원문 번호를 그대로 줍니다. 건너뛴 번호는 빈 자리로 남아야 어디가 빠졌는지 보입니다 */
  it('번호를 건너뛰면(1, 2, 4) 4번 내용은 4번 자리에, 3번은 비워 둔다', () => {
    const slide = (n: number, content: string) => ({
      slide_number: n,
      content,
      keywords: [],
      highlights: [],
    });
    const progress = fromScriptDetail({
      ...base,
      parse_status: 'DONE',
      segmented: true,
      slides: [slide(1, '가'), slide(4, '라'), slide(2, '나')],
    });
    expect(progress).toEqual({ status: 'done', blocks: ['가', '나', '', '라'], segmented: true });
  });

  it('번호를 자리로 쓸 수 없으면(0 · 너무 큼) 번호 순서대로만 붙인다', () => {
    const slide = (n: number, content: string) => ({
      slide_number: n,
      content,
      keywords: [],
      highlights: [],
    });
    const progress = fromScriptDetail({
      ...base,
      parse_status: 'DONE',
      segmented: true,
      slides: [slide(2026, '나'), slide(0, '가')],
    });
    expect(progress).toEqual({ status: 'done', blocks: ['가', '나'], segmented: true });
  });

  it('FAILED 면 원인 코드를 넘긴다', () => {
    expect(fromScriptDetail({ ...base, parse_status: 'FAILED', error_code: 'AI_TIMEOUT' })).toEqual(
      { status: 'failed', errorCode: 'AI_TIMEOUT' },
    );
  });
});
