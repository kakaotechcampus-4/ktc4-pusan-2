import { forwardRef, type ButtonHTMLAttributes } from 'react';
import { Link, type LinkProps } from 'react-router';

type ButtonVariant = 'primary' | 'secondary' | 'quiet';
type ButtonSize = 'sm' | 'md';

const variants: Record<ButtonVariant, string> = {
  primary:
    'border-ink bg-coral-deep text-white shadow-[3px_3px_0_var(--color-ink)] hover:bg-ink disabled:border-line-strong disabled:bg-cream aria-disabled:border-line-strong aria-disabled:bg-cream',
  secondary:
    'border-ink bg-panel text-ink hover:bg-cream disabled:border-line-strong disabled:bg-cream aria-disabled:border-line-strong aria-disabled:bg-cream',
  quiet: 'border-transparent bg-transparent text-ink hover:bg-cream',
};
const sizes: Record<ButtonSize, string> = {
  sm: 'min-h-9 px-3 py-1.5 text-xs',
  md: 'min-h-11 px-6 py-2.5 text-sm',
};

function styles(variant: ButtonVariant, size: ButtonSize, className = '') {
  return `inline-flex max-w-full items-center justify-center gap-2 border-2 text-center font-bold transition-colors disabled:pointer-events-none disabled:text-ink/80 disabled:shadow-none aria-disabled:pointer-events-none aria-disabled:text-ink/80 aria-disabled:shadow-none ${variants[variant]} ${sizes[size]} ${className}`;
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

/**
 * 동작은 Button, 화면 이동은 ButtonLink. 기본값으로 폼을 제출하지 않습니다.
 * ref 는 창을 닫은 뒤 초점을 되돌리는 곳이 씁니다 (제품 DeviceCheckPage)
 *
 * 누른 버튼이 그 자리에서 막히면(저장 중 등) `disabled` 대신 `aria-disabled` 를 줍니다.
 * `disabled` 는 쥐고 있던 초점을 body 로 떨어뜨립니다 (#818 · #845). 모양은 둘이 같습니다.
 * `aria-disabled` 는 마우스만 막아(pointer-events-none) Enter · Space 는 그대로 click 을 냅니다 —
 * 그래서 여기서 onClick 을 부르지 않고 제출도 막습니다. 쓰는 곳마다 가드를 적지 않아도 됩니다 (#874 리뷰)
 */
export const Button = forwardRef<HTMLButtonElement, ButtonProps>(function Button(
  { variant = 'primary', size = 'md', type = 'button', className, onClick, ...props },
  ref,
) {
  const blocked = props['aria-disabled'] === true || props['aria-disabled'] === 'true';
  return (
    <button
      {...props}
      ref={ref}
      type={type}
      onClick={(event) => {
        if (blocked) {
          event.preventDefault();
          return;
        }
        onClick?.(event);
      }}
      className={styles(variant, size, className)}
    />
  );
});

export function ButtonLink({
  variant = 'primary',
  size = 'md',
  className,
  ...props
}: LinkProps & { variant?: ButtonVariant; size?: ButtonSize }) {
  return <Link {...props} className={styles(variant, size, className)} />;
}
