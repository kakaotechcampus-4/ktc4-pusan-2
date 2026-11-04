/**
 * 대화형 코치 에이전트를 화면에 띄울지 — 빌드 스위치.
 *
 * AI 서비스 탑재 · BE 화면용 API 전이라 기본은 꺼 둡니다. 켜야 버튼이 보이고, 켜도 서버의 사용 가능 여부
 * (`{enabled:false}` · `AGENT_DISABLED`)가 꺼짐이면 숨깁니다(`docs/ai/features/chat-agent.md` 12-4 F1).
 * 미설정은 꺼짐이고, 바꾸면 다시 빌드해야 합니다.
 */
export const isChatAgentBuildEnabled = () => import.meta.env.VITE_CHAT_AGENT === 'true';
