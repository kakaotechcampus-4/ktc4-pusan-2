import { useEffect, useId, useRef, useState } from 'react';
import { submitSuggestion } from '@/shared/api/serviceReview';
import { ApiFailure } from '@/shared/api/tokenStore';
import { toMessage } from '@/shared/api/errorMessage';
import { Card } from '@/shared/ui/Panel';
import { Button } from '@/shared/ui/Button';
import { SuggestionCategorySelect } from '@/shared/ui/SuggestionCategorySelect';
import type { SuggestionCategory, SuggestionRequest } from '@/types/serviceReview';

export function SuggestionForm() {
  const id = useId();
  const [content, setContent] = useState('');
  const [category, setCategory] = useState<SuggestionCategory>('OTHER');
  const [pending, setPending] = useState(false);
  const [error, setError] = useState('');
  const [sent, setSent] = useState(false);
  const [request, setRequest] = useState<SuggestionRequest | null>(null);
  const controller = useRef<AbortController | null>(null);
  const alive = useRef(true);
  useEffect(() => {
    alive.current = true;
    return () => {
      alive.current = false;
      controller.current?.abort();
    };
  }, []);
  const length = [...content.trim()].length;
  async function send() {
    if (controller.current || !length || length > 2000) return;
    const outgoing = request ?? {
      request_id: crypto.randomUUID(),
      content: content.trim(),
      category,
    };
    setRequest(outgoing);
    controller.current = new AbortController();
    setPending(true);
    setError('');
    setSent(false);
    try {
      await submitSuggestion(outgoing, controller.current.signal);
      if (!alive.current) return;
      setRequest(null);
      setContent('');
      setCategory('OTHER');
      setSent(true);
    } catch (failure) {
      if (!alive.current) return;
      if (failure instanceof ApiFailure && failure.status === 422) setRequest(null);
      setError(
        failure instanceof ApiFailure && failure.code === 'SUGGESTION_REQUEST_CONFLICT'
          ? '이 전송의 내용이 이전 요청과 달라요. 자동으로 다시 보내지 않았어요. 새 의견으로 보내려면 입력을 새로 시작해 주세요.'
          : '의견을 보내지 못했어요. ' + toMessage(failure),
      );
    } finally {
      controller.current = null;
      if (alive.current) setPending(false);
    }
  }
  const submitLabel = error ? '같은 의견 다시 보내기' : '의견 보내기';
  return (
    <Card title="의견 보내기">
      <p className="text-sm text-ink/80">
        좋았던 점이나 개선할 점을 알려주세요. 별점과 관계없이 언제든 보낼 수 있어요.
      </p>
      <form
        className="flex min-w-0 flex-col gap-3"
        onSubmit={(event) => {
          event.preventDefault();
          send().catch(() => setError('다시 시도해 주세요.'));
        }}
      >
        <SuggestionCategorySelect
          value={category}
          onChange={(value) => {
            setCategory(value);
            setSent(false);
          }}
          disabled={pending || Boolean(request)}
        />
        <label htmlFor={id} className="text-sm font-bold">
          의견 (필수)
        </label>
        <textarea
          id={id}
          required
          rows={5}
          value={content}
          readOnly={pending || Boolean(request)}
          aria-describedby={`${id}-count`}
          onChange={(event) => {
            setContent(event.target.value);
            setSent(false);
          }}
          className="w-full min-w-0 resize-y border-2 border-ink bg-white p-3 text-ink focus-visible:outline focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-ink"
        />
        <p id={`${id}-count`} className="text-sm text-ink/80">
          {length.toLocaleString()} / 2,000자
        </p>
        {error && (
          <p role="alert" className="text-sm">
            {error}
          </p>
        )}
        {sent && (
          <p role="status" className="text-sm">
            의견을 보냈어요. 감사합니다.
          </p>
        )}
        <div className="flex flex-wrap gap-3">
          <Button type="submit" disabled={pending || !length || length > 2000}>
            {pending ? '보내는 중…' : submitLabel}
          </Button>
          {error && request && (
            <Button
              variant="quiet"
              type="button"
              onClick={() => {
                if (
                  !window.confirm(
                    '이전 요청이 이미 저장됐을 수 있어요. 새로 보내면 의견이 추가될 수 있습니다. 새 입력을 시작할까요?',
                  )
                )
                  return;
                setRequest(null);
                setError('');
                setContent('');
                setCategory('OTHER');
              }}
            >
              새 의견 작성
            </Button>
          )}
        </div>
      </form>
    </Card>
  );
}
