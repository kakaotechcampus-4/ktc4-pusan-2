# docs/be

피치코치 BE 기술 문서입니다. 설치 · 실행 · API 목록은 [backend/README.md](../../backend/README.md) 에 있고,
여기에는 여러 모듈에 걸친 동작을 따로 정리합니다.

## 문서 지도

| 문서 | 담는 것 |
|---|---|
| [take-lifecycle.md](take-lifecycle.md) | Take 가 실시간 STT 에 붙고, 끊기면 다시 붙고, 언제 어떤 상태로 끝나는지. 재연결 · 대기 시간 · 자동 종료 · 되살리기 · PUT 규칙 |
| [realtime-stt.md](realtime-stt.md) | 실시간 STT WebSocket 메시지 순서 · 에러 코드 · 재연결 · 전사 · 1초 원자료 저장 · 종료 · 자동 종료 |
| [script-parse.md](script-parse.md) | 발표자료 · 대본 업로드, AI 대본 파싱 호출 · 재시도 · 만료 · 조건부 저장 |
| [realtime-coach.md](realtime-coach.md) | 발표 중 코치 · 종료 뒤 Take 결과 · 코칭 계획 · 원자료 보관 |
| [file-split-plan.md](file-split-plan.md) | 백엔드 폴더 구조를 나눈 기준 · 계획 · 적용 결과 |
| [take-survey.md](take-survey.md) | 리포트마다 받는 Take별 설문 · 테스트 참여 동의 · 1차 배포 별점 삭제 · FE 전달 사항 |
| [service-feedback-plan-540.md](service-feedback-plan-540.md) | 평점 팝업·마이페이지 의견 API · 테이블 3개와 ERD · 동시 처리 · FE 연동 |
