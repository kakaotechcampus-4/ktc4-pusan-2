import { Outlet } from 'react-router';
import { ChatAgentHost } from '@/features/chat/ChatAgentHost';

/**
 * Route-independent layer: the chat host stays mounted when a page changes.
 * The existing route guards, headers and page layouts remain untouched.
 */
export function AppShell() {
  return (
    <>
      <Outlet />
      <ChatAgentHost />
    </>
  );
}
