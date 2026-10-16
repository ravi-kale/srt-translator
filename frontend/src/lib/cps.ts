// Mirrors backend/subtrans/text_metrics.py so edits show the same CPS before the server confirms.
const TAG_RE = /<[^>]+>|\{\\[^}]*\}/g;

export function visibleLength(text: string): number {
  return [...text.replace(TAG_RE, "").replace(/\r/g, "").replace(/\n/g, "")].length;
}

export function computeCps(text: string, durationSec: number): number {
  const chars = visibleLength(text);
  if (chars === 0) return 0;
  return Math.round((chars / Math.max(durationSec, 0.01)) * 10) / 10;
}

export type CpsTone = "none" | "ok" | "warn" | "danger";

export function cpsTone(cps: number, threshold: number): CpsTone {
  if (cps === 0) return "none";
  if (cps > threshold) return "danger";
  if (cps > threshold * 0.85) return "warn";
  return "ok";
}

export function formatDuration(sec: number): string {
  return `${sec.toFixed(1)}s`;
}

export function formatUsd(v: number): string {
  if (v === 0) return "$0.00";
  if (v < 0.01) return `$${v.toFixed(4)}`;
  if (v < 1) return `$${v.toFixed(3)}`;
  return `$${v.toFixed(2)}`;
}

export function formatTokens(n: number): string {
  if (n >= 1_000_000) return `${(n / 1_000_000).toFixed(2)}M`;
  if (n >= 1_000) return `${(n / 1_000).toFixed(1)}k`;
  return String(n);
}

export function formatElapsed(seconds: number): string {
  const s = Math.max(0, Math.round(seconds));
  const m = Math.floor(s / 60);
  const h = Math.floor(m / 60);
  if (h) return `${h}h ${m % 60}m`;
  if (m) return `${m}m ${s % 60}s`;
  return `${s}s`;
}
