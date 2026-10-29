/** 화면 설계서 진입점. 제품 로그인 · 목 서버 · API · 장치 없이 뷰어만 연다. */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter } from 'react-router';
import { DesignRoutes } from './DesignGuide';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <DesignRoutes />
    </HashRouter>
  </StrictMode>,
);
