import type { ButtonHTMLAttributes } from 'react';
import { Link, type LinkProps } from 'react-router';

type ButtonVariant = 'primary' | 'secondary' | 'quiet';
type ButtonSize = 'sm' | 'md';

const variants: Record<ButtonVariant, string> = {
  primary:
    'border-ink bg-coral-deep text-white shadow-[3px_3px_0_var(--color-ink)] hover:bg-ink disabled:border-line-strong disabled:bg-cream',
  secondary:
    'border-ink bg-panel text-ink hover:bg-cream disabled:border-line-strong disabled:bg-cream',
  quiet: 'border-transparent bg-transparent text-ink hover:bg-cream',
};
const sizes: Record<ButtonSize, string> = {
  sm: 'min-h-9 px-3 py-1.5 text-xs',
  md: 'min-h-11 px-6 py-2.5 text-sm',
};

function styles(variant: ButtonVariant, size: ButtonSize, className = '') {
  return `inline-flex max-w-full items-center justify-center gap-2 border-2 text-center font-bold transition-colors disabled:pointer-events-none disabled:text-ink/80 disabled:shadow-none ${variants[variant]} ${sizes[size]} ${className}`;
}

export interface ButtonProps extends ButtonHTMLAttributes<HTMLButtonElement> {
  variant?: ButtonVariant;
  size?: ButtonSize;
}

/** 동작은 Button, 화면 이동은 ButtonLink. 기본값으로 폼을 제출하지 않습니다. */
export function Button({
  variant = 'primary',
  size = 'md',
  type = 'button',
  className,
  ...props
}: ButtonProps) {
  return <button {...props} type={type} className={styles(variant, size, className)} />;
}

export function ButtonLink({
  variant = 'primary',
  size = 'md',
  className,
  ...props
}: LinkProps & { variant?: ButtonVariant; size?: ButtonSize }) {
  return <Link {...props} className={styles(variant, size, className)} />;
}
