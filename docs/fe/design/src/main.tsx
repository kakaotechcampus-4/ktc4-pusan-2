/** 화면 설계서 진입점. 제품 로그인 · 목 서버 · API · 장치 없이 시안만 연다. */
import { StrictMode } from 'react';
import { createRoot } from 'react-dom/client';
import { HashRouter, Navigate, Route, Routes } from 'react-router';
import { DesignDraftRoute, DesignIndex } from './drafts/DesignIndex';
import './styles.css';

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    <HashRouter>
      <Routes>
        <Route path="/" element={<DesignIndex />} />
        <Route path="/:name" element={<DesignDraftRoute />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </HashRouter>
  </StrictMode>,
);
