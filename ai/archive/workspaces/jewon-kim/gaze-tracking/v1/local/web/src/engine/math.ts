/**
 * Numerics the engine needs and JavaScript lacks: the normal CDF in log space
 * (scipy `log_ndtr`), `logsumexp`, and numpy-style medians.
 *
 * `logNdtr` follows scipy's branch structure exactly (`a > 6`, `a > -20`, the
 * asymptotic series below) so the soft-box densities match the Python engine;
 * the parity tests compare against scipy at every branch.
 */

const SQRT1_2 = Math.SQRT1_2;
const INV_SQRT_PI = 1 / Math.sqrt(Math.PI);
const TINY = 1e-300;

/** erf for |x| < 2 via the all-positive series (no cancellation). */
function erfSeries(x: number): number {
  const x2 = x * x;
  let term = x;
  let sum = x;
  for (let n = 1; n < 200; n++) {
    term *= (2 * x2) / (2 * n + 1);
    sum += term;
    if (term < 1e-17 * sum) break;
  }
  return 2 * INV_SQRT_PI * Math.exp(-x2) * sum;
}

/** erfc for x >= 2 via the continued fraction (modified Lentz). */
function erfcContinuedFraction(x: number): number {
  let f = x;
  let c = x;
  let d = 0;
  for (let n = 1; n < 500; n++) {
    const a = n / 2;
    d = x + a * d;
    if (Math.abs(d) < TINY) d = TINY;
    d = 1 / d;
    c = x + a / c;
    if (Math.abs(c) < TINY) c = TINY;
    const delta = c * d;
    f *= delta;
    if (Math.abs(delta - 1) < 1e-16) break;
  }
  return (Math.exp(-x * x) * INV_SQRT_PI) / f;
}

export function erfc(x: number): number {
  if (Number.isNaN(x)) return NaN;
  if (x < 0) return 2 - erfc(-x);
  if (x < 2) return 1 - erfSeries(x);
  return erfcContinuedFraction(x);
}

/** Standard normal CDF. */
export function ndtr(a: number): number {
  return 0.5 * erfc(-a * SQRT1_2);
}

/** `log(ndtr(a))`, accurate in both tails (scipy `log_ndtr`). */
export function logNdtr(a: number): number {
  if (Number.isNaN(a)) return NaN;
  if (a > 6) return -ndtr(-a);
  if (a > -20) return Math.log(ndtr(a));
  const logLhs = -0.5 * a * a - Math.log(-a) - 0.5 * Math.log(2 * Math.PI);
  const denomCons = 1 / (a * a);
  let lastTotal = 0;
  let rhs = 1;
  let numerator = 1;
  let denomFactor = 1;
  let sign = 1;
  let i = 0;
  while (Math.abs(lastTotal - rhs) > Number.EPSILON) {
    i += 1;
    lastTotal = rhs;
    sign = -sign;
    denomFactor *= denomCons;
    numerator *= 2 * i - 1;
    rhs += sign * numerator * denomFactor;
  }
  return logLhs + Math.log(rhs);
}

/** `log(sum(exp(v)))` without overflow; `-Infinity` for an empty or all `-Infinity` input. */
export function logsumexp(values: readonly number[]): number {
  let max = -Infinity;
  for (const v of values) if (v > max) max = v;
  if (!Number.isFinite(max)) return max;
  let sum = 0;
  for (const v of values) sum += Math.exp(v - max);
  return max + Math.log(sum);
}

/** numpy-style median (mean of the two middle values); `NaN` when empty. */
export function median(values: readonly number[]): number {
  if (values.length === 0) return NaN;
  const s = [...values].sort((a, b) => a - b);
  const mid = s.length >> 1;
  return s.length % 2 ? (s[mid] as number) : ((s[mid - 1] as number) + (s[mid] as number)) / 2;
}

/** `1.4826 x MAD`: a Gaussian-consistent robust spread. */
export const MAD_TO_SIGMA = 1.4826;

export function finite(...values: number[]): boolean {
  return values.every((v) => Number.isFinite(v));
}

export function clamp(v: number, lo: number, hi: number): number {
  return v < lo ? lo : v > hi ? hi : v;
}
