// 관리자 콘솔. /admin/api/* 를 불러 화면을 채운다.
// 로그인은 브라우저가 처리한다 — /admin/ 에서 입력한 계정을 같은 경로 아래 요청에 자동으로 붙인다.
// 사용자 이름 · 발표 제목은 사용자가 정한 값이라 innerHTML 에 넣지 않고 textContent 로만 넣는다.

const LABELS = {
  READY: '시작 전',
  RUNNING: '진행 중',
  ANALYZING: '분석 중',
  COMPLETED: '완료',
  FAILED: '실패',
  COACHING: '코칭',
  EXAM: '실전',
  HIGHLIGHT: '하이라이트',
  KEYWORD: '키워드',
  OFF: '끔',
  SILENCE: '말 없음',
  TIME_LIMIT: '최대 시간',
  ABANDONED: '이탈',
  CLOSED: '창 닫음',
  USER: '직접 종료',
  RATING_POPUP: '평점 팝업',
  MY_PAGE: '마이페이지',
  POSITIVE: '좋았던 점',
  IMPROVEMENT: '개선할 점',
  PROBLEM: '문제 신고',
  QUESTION: '질문',
  OTHER: '기타',
};

const label = (value) => (value == null ? '—' : LABELS[value] ?? value);

function duration(sec) {
  if (sec == null) return '—';
  const s = Math.round(sec);
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  if (h > 0) return `${h}시간 ${m}분`;
  if (m > 0) return `${m}분 ${s % 60}초`;
  return `${s}초`;
}

function dateTime(iso) {
  if (!iso) return '—';
  return new Date(iso).toLocaleString('ko-KR', {
    timeZone: 'Asia/Seoul',
    month: 'numeric',
    day: 'numeric',
    hour: '2-digit',
    minute: '2-digit',
  });
}

const percent = (ratio) => (ratio == null ? '—' : `${Math.round(ratio * 100)}%`);

function el(tag, props = {}, children = []) {
  const node = document.createElement(tag);
  Object.assign(node, props);
  node.append(...children);
  return node;
}

async function getJSON(path) {
  const response = await fetch(path, { headers: { Accept: 'application/json' } });
  if (!response.ok) throw new Error(`${path} — ${response.status}`);
  return response.json();
}

// ---------- 요약 카드 ----------

function renderCards(stats) {
  const { users, takes, practice_time: time } = stats;
  const card = (name, value, sub = '') =>
    el('div', { className: 'card' }, [
      el('div', { className: 'label', textContent: name }),
      el('div', { className: 'value', textContent: value }),
      el('div', { className: 'sub', textContent: sub }),
    ]);
  document.getElementById('cards').replaceChildren(
    card('사용자', users.total.toLocaleString(), `오늘 가입 ${users.today}`),
    card('연습', takes.total.toLocaleString(), `오늘 ${takes.today}`),
    card('총 연습 시간', duration(time.total_sec), `시간을 잰 연습 ${time.measured_takes}개`),
    card('평균 연습 시간', duration(time.avg_sec), `중앙값 ${duration(time.median_sec)}`),
    card('목표 시간 대비', percent(time.avg_goal_ratio), '실제 ÷ 목표 평균'),
  );
}

// ---------- 일별 막대 ----------

// '2026-10-22' → '10/22'
const shortDate = (iso) => iso.slice(5).split('-').map(Number).join('/');

function renderBars(id, daily, key) {
  const max = Math.max(1, ...daily.map((d) => d[key]));
  const total = daily.reduce((sum, d) => sum + d[key], 0);
  // 막대 위 숫자(0 이면 비움), 막대, 아래 날짜. 날짜는 오늘부터 7일 간격으로만 적는다
  const columns = daily.map((d) =>
    el('div', { className: 'col', title: `${d.date} · ${d[key]}` }, [
      el('span', { className: 'count', textContent: d[key] ? String(d[key]) : '' }),
      el('div', {
        className: d[key] ? 'bar' : 'bar empty',
        // 위 숫자 칸(14px)을 뺀 높이에서 비율만큼
        style: `height: calc((100% - 14px) * ${d[key] / max})`,
      }),
    ]),
  );
  const labels = daily.map((d, i) =>
    el('span', { textContent: (daily.length - 1 - i) % 7 === 0 ? shortDate(d.date) : '' }),
  );
  document.getElementById(id).replaceChildren(
    el('div', { className: 'plot' }, columns),
    el('div', { className: 'axis' }, labels),
    el('div', { className: 'total', textContent: `${daily.length}일 합계 ${total.toLocaleString()}` }),
  );
}

// ---------- 표 ----------

// columns: [제목, 값을 꺼내는 함수, 숫자 칸이면 true]
function renderTable(id, columns, rows, emptyText = '없음') {
  const head = el('tr', {}, columns.map(([title, , num]) => el('th', { className: num ? 'num' : '', textContent: title })));
  const body = rows.length
    ? rows.map((row) =>
        el('tr', {}, columns.map(([, pick, num]) => {
          const value = pick(row);
          const cell = el('td', { className: num ? 'num' : '' });
          cell.append(value instanceof Node ? value : String(value));
          return cell;
        })),
      )
    : [el('tr', { className: 'empty' }, [el('td', { colSpan: columns.length, textContent: emptyText })])];
  document.getElementById(id).replaceChildren(el('thead', {}, [head]), el('tbody', {}, body));
}

function renderCounts(id, counts) {
  const total = Object.values(counts).reduce((a, b) => a + b, 0);
  const rows = Object.entries(counts).sort((a, b) => b[1] - a[1]);
  renderTable(id, [
    ['값', ([key]) => label(key)],
    ['개수', ([, n]) => n, true],
    ['비율', ([, n]) => percent(n / total), true],
  ], rows);
}

const statusBadge = (status) => el('span', { className: `badge ${status}`, textContent: label(status) });

const TAKE_COLUMNS = [
  ['만든 시각', (t) => dateTime(t.created_at)],
  ['사용자', (t) => `${t.user_name} (${t.user_email})`],
  ['발표', (t) => t.pitch_title],
  ['회차', (t) => t.take_number, true],
  ['모드', (t) => label(t.mode)],
  ['상태', (t) => statusBadge(t.status)],
  ['종료 이유', (t) => (t.ended_at ? label(t.end_reason ?? 'USER') : '—')],
  ['연습 시간', (t) => duration(t.elapsed_sec), true],
  ['목표', (t) => duration(t.goal_time_sec), true],
  ['기록', liveButton],
];

const USER_COLUMNS = [
  ['이름', (u) => u.name],
  ['이메일', (u) => u.email],
  ['가입', (u) => dateTime(u.created_at)],
  ['발표', (u) => u.pitch_count, true],
  ['연습', (u) => u.take_count, true],
  ['총 연습 시간', (u) => duration(u.total_practice_sec), true],
  ['마지막 연습', (u) => dateTime(u.last_take_at)],
];

// ---------- 서비스 평점 · 의견 ----------

const opinionText = (text) => el('span', { className: 'opinion', textContent: text ?? '—' });
const FEEDBACK_COLUMNS = [
  ['제출 시각', (row) => dateTime(row.created_at)],
  ['사용자 ID', (row) => row.user_id ?? '탈퇴한 사용자'],
  ['평점', (row) => `${row.rating} / 5`, true],
  ['의견 유형', (row) => label(row.category)],
  ['의견', (row) => opinionText(row.suggestion)],
];
const SUGGESTION_COLUMNS = [
  ['제출 시각', (row) => dateTime(row.created_at)],
  ['사용자 ID', (row) => row.user_id ?? '탈퇴한 사용자'],
  ['보낸 곳', (row) => label(row.source)],
  ['의견 유형', (row) => label(row.category)],
  ['의견', (row) => opinionText(row.content)],
];

// 사용자별 평점 · 의견. 한 사용자가 한 행이고 의견은 같은 칸에 목록으로 모은다
const stars = (rating) => '★'.repeat(rating) + '☆'.repeat(5 - rating);
const subText = (text) => el('small', { className: 'muted block', textContent: text });

// UUIDv7 은 앞자리가 만든 시각이라 비슷한 때 가입한 사용자끼리 겹친다. 뒷자리를 붙여 가른다.
// 전체 ID 는 마우스를 올리면 보인다
const shortId = (id) => el('span', { title: id, textContent: `${id.slice(0, 8)}…${id.slice(-4)}` });

function authorCell(row) {
  if (row.user_id) return shortId(row.user_id);
  // 탈퇴하면 작성자를 알 수 없다. 묶음마다 다른 ID 를 보여 서로 다른 탈퇴자를 가른다
  const retained = el('small', { className: 'muted block' }, ['작성자 연결 없음 · ', shortId(row.retained_id)]);
  return el('span', {}, ['탈퇴한 사용자', retained]);
}

function renderRatingSummary(summary) {
  const node = document.getElementById('rating-summary');
  if (summary.avg_rating == null) {
    node.replaceChildren(el('span', { className: 'muted', textContent: '아직 평점이 없습니다' }));
    return;
  }
  node.replaceChildren(
    '평균 ',
    el('strong', { textContent: `${summary.avg_rating.toFixed(1)}점` }),
    el('span', { className: 'muted', textContent: ` · 평점 ${summary.rated_count}개, 미평가 제외` }),
  );
}

function ratingCell(row) {
  if (row.rating == null) return el('span', { className: 'muted', textContent: '미평가' });
  return el('span', {}, [`${stars(row.rating)} · ${row.rating}점`, subText(dateTime(row.rated_at))]);
}

const suggestionItem = (s) =>
  el('li', {}, [
    opinionText(s.content),
    subText(`${label(s.source)} · ${label(s.category)} · ${dateTime(s.created_at)}`),
  ]);

function suggestionsCell(row) {
  if (row.suggestion_count === 0) return el('span', { className: 'muted', textContent: '의견 없음' });
  const list = el('ul', { className: 'opinions' }, row.suggestions.map(suggestionItem));
  // 탈퇴한 사용자의 묶음은 의견이 하나라 더 받을 것이 없다
  if (!row.user_id || row.suggestions.length >= row.suggestion_count) return list;
  // 나머지는 의견 목록을 이 사용자로 걸러 같은 순서로 이어서 받는다.
  // 그 사이 새 의견이 오면 순서가 한 칸씩 밀려 받은 의견이 다시 올 수 있어 ID 로 거른다
  const shown = new Set(row.suggestions.map((s) => s.id));
  let offset = row.suggestions.length;
  const more = el('button', { type: 'button', className: 'more' });
  const error = el('small', { className: 'error-text block', hidden: true });
  const updateMore = () => {
    more.textContent = `의견 더보기 (${Math.max(0, row.suggestion_count - shown.size)}개 남음)`;
  };
  updateMore();
  more.addEventListener('click', async () => {
    more.disabled = true;
    error.hidden = true;
    const query = new URLSearchParams({ user_id: row.user_id, limit: '50', offset: String(offset) });
    try {
      const page = await getJSON(`api/suggestions?${query}`);
      offset += page.items.length;
      const fresh = page.items.filter((s) => !shown.has(s.id));
      fresh.forEach((s) => shown.add(s.id));
      list.append(...fresh.map(suggestionItem));
      if (page.next_offset === null) more.remove();
      else updateMore();
    } catch (failure) {
      error.textContent = `더 불러오지 못했습니다 — ${failure.message}`;
      error.hidden = false;
    } finally {
      more.disabled = false;
    }
  });
  return el('div', {}, [list, more, error]);
}

const FEEDBACK_USER_COLUMNS = [
  ['사용자', authorCell],
  ['평점', ratingCell],
  ['의견들', suggestionsCell],
  ['마지막 제출', (row) => dateTime(row.last_at)],
];

const feedbackPages = {
  feedbacks: { path: 'api/service-feedbacks', columns: FEEDBACK_COLUMNS, offset: 0, next: null, request: 0 },
  suggestions: { path: 'api/suggestions', columns: SUGGESTION_COLUMNS, offset: 0, next: null, request: 0 },
  'feedback-users': {
    path: 'api/service-feedbacks/by-user',
    columns: FEEDBACK_USER_COLUMNS,
    offset: 0,
    next: null,
    request: 0,
  },
};

async function loadFeedbackPage(id, offset) {
  const state = feedbackPages[id];
  const request = ++state.request;
  const previous = document.getElementById(`${id}-prev`);
  const next = document.getElementById(`${id}-next`);
  const error = document.getElementById(`${id}-error`);
  previous.disabled = next.disabled = true;
  error.hidden = true;
  const query = new URLSearchParams({ limit: '50', offset: String(offset) });
  if (id === 'suggestions') {
    const source = document.getElementById('suggestions-source').value;
    if (source) query.set('source', source);
    const category = document.getElementById('suggestions-category').value;
    if (category) query.set('category', category);
  }
  try {
    const page = await getJSON(`${state.path}?${query}`);
    if (request !== state.request) return;
    state.offset = offset;
    state.next = page.next_offset;
    renderTable(id, state.columns, page.items);
    document.getElementById(`${id}-page`).textContent = `${offset / 50 + 1}페이지`;
  } catch (failure) {
    if (request !== state.request) return;
    // 이전 필터의 내용이 현재 필터 결과처럼 남지 않게 지운다.
    state.offset = 0;
    state.next = null;
    renderTable(id, state.columns, [], '불러오지 못했습니다');
    document.getElementById(`${id}-page`).textContent = '';
    error.textContent = failure.message;
    error.hidden = false;
  } finally {
    if (request === state.request) {
      previous.disabled = state.offset === 0;
      next.disabled = state.next === null;
    }
  }
}

for (const [id, state] of Object.entries(feedbackPages)) {
  document.getElementById(`${id}-prev`).addEventListener('click', () => {
    loadFeedbackPage(id, Math.max(0, state.offset - 50)).catch(showFeedbackError);
  });
  document.getElementById(`${id}-next`).addEventListener('click', () => {
    if (state.next !== null) loadFeedbackPage(id, state.next).catch(showFeedbackError);
  });
}
function showFeedbackError(error) {
  const node = document.getElementById('error');
  node.textContent = error.message;
  node.hidden = false;
}
for (const id of ['suggestions-source', 'suggestions-category']) {
  document.getElementById(id).addEventListener('change', () => {
    loadFeedbackPage('suggestions', 0).catch(showFeedbackError);
  });
}

// ---------- 연습 기록 ----------

const LIVE_POLL_MS = 3000;
const LIVE_STATUSES = ['READY', 'RUNNING'];
const live = { takeId: null, timer: null, request: 0, segments: [], seconds: [] };

function clock(ms) {
  const s = Math.floor(ms / 1000);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

const record = (value) =>
  el('code', { className: 'record', textContent: value == null ? '—' : JSON.stringify(value) });

const SEGMENT_COLUMNS = [
  ['시각', (s) => `${clock(s.start_ms)}–${clock(s.end_ms)}`],
  ['문장', (s) => opinionText(s.transcript)],
  ['신뢰도', (s) => s.confidence.toFixed(2), true],
  ['STT 세션', (s) => s.stt_session_no, true],
];

const SECOND_COLUMNS = [
  ['시각', (s) => clock(s.t_ms)],
  ['장', (s) => s.slide_number ?? '—', true],
  ['STT 상태', (s) => s.stt_status],
  ['시선', (s) => record(s.gaze)],
  ['음량', (s) => record(s.voice)],
];

function liveButton(take) {
  const button = el('button', { type: 'button', textContent: '보기' });
  button.addEventListener('click', () => openLive(take));
  return button;
}

function openLive(take) {
  clearTimeout(live.timer);
  live.request += 1;
  Object.assign(live, { takeId: take.id, timer: null, segments: [], seconds: [] });
  document.getElementById('live-title').textContent =
    `${take.user_name} · ${take.pitch_title} · ${take.take_number}회차`;
  document.getElementById('live-error').hidden = true;
  const panel = document.getElementById('live');
  panel.hidden = false;
  renderLive(take.status);
  pollLive();
  panel.scrollIntoView({ behavior: 'smooth' });
}

function closeLive() {
  clearTimeout(live.timer);
  live.request += 1;
  Object.assign(live, { takeId: null, timer: null, segments: [], seconds: [] });
  document.getElementById('live').hidden = true;
}

async function pollLive() {
  const request = ++live.request;
  const error = document.getElementById('live-error');
  const query = new URLSearchParams({ after_seq: String(live.segments.at(-1)?.seq ?? 0) });
  const last = live.seconds.at(-1);
  if (last) query.set('after_t_ms', String(last.t_ms));
  try {
    const data = await getJSON(`api/takes/${live.takeId}/live?${query}`);
    if (request !== live.request) return;
    live.segments.push(...data.segments);
    live.seconds.push(...data.seconds);
    error.hidden = true;
    renderLive(data.status);
    const running = LIVE_STATUSES.includes(data.status);
    const more = data.segments.length > 0 || data.seconds.length > 0;
    if (running || more) live.timer = setTimeout(pollLive, running ? LIVE_POLL_MS : 0);
  } catch (failure) {
    if (request !== live.request) return;
    error.textContent = `불러오지 못했습니다 — ${failure.message}`;
    error.hidden = false;
    live.timer = setTimeout(pollLive, LIVE_POLL_MS);
  }
}

function renderLive(status) {
  document.getElementById('live-status').replaceChildren(
    statusBadge(status),
    el('span', {
      className: 'muted',
      textContent: ` 전사 ${live.segments.length}개 · 1초 기록 ${live.seconds.length}개`,
    }),
  );
  renderTable('live-segments', SEGMENT_COLUMNS, [...live.segments].reverse(), '아직 저장된 전사가 없습니다');
  renderTable(
    'live-seconds',
    SECOND_COLUMNS,
    [...live.seconds].reverse(),
    '1초 기록이 없습니다 — 동의하지 않은 연습은 리포트를 만든 뒤 지웁니다',
  );
}

document.getElementById('live-close').addEventListener('click', closeLive);

// ---------- 불러오기 ----------

// 하나가 실패해도 나머지는 그린다. 실패를 보려고 연 화면에서 전부 비면 안 된다
const SECTIONS = [
  ['api/stats', (stats) => {
    renderCards(stats);
    document.querySelectorAll('.days').forEach((node) => {
      node.textContent = `최근 ${stats.daily.length}일`;
    });
    renderBars('daily-takes', stats.daily, 'takes');
    renderBars('daily-signups', stats.daily, 'signups');
    renderCounts('by-status', stats.takes.by_status);
    renderCounts('by-mode', stats.takes.by_mode);
    renderCounts('by-script-mode', stats.takes.by_script_mode);
    renderCounts('by-end-reason', stats.takes.by_end_reason);
  }],
  ['api/takes/stuck', (rows) => renderTable('stuck-takes', TAKE_COLUMNS, rows)],
  ['api/takes?status=FAILED&limit=50', (rows) => renderTable('failed-takes', TAKE_COLUMNS, rows)],
  ['api/takes?limit=50', (rows) => renderTable('recent-takes', TAKE_COLUMNS, rows)],
  ['api/users', (rows) => renderTable('users', USER_COLUMNS, rows)],
  ['api/service-feedbacks/summary', renderRatingSummary],
];

async function load() {
  const error = document.getElementById('error');
  error.hidden = true;
  const requests = SECTIONS.map(([path]) => getJSON(path));
  const feedbackRequests = Object.keys(feedbackPages).map((id) => loadFeedbackPage(id, 0));
  const results = await Promise.allSettled(requests);
  await Promise.all(feedbackRequests);
  const failures = [];
  results.forEach((result, i) => {
    if (result.status === 'fulfilled') SECTIONS[i][1](result.value);
    else failures.push(result.reason.message);
  });
  document.getElementById('updated').textContent = `${dateTime(new Date().toISOString())} 기준`;
  if (failures.length) {
    error.textContent = `불러오지 못했습니다: ${failures.join(', ')}`;
    error.hidden = false;
  }
}

document.getElementById('refresh').addEventListener('click', load);
load();
