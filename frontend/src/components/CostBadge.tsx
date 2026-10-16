import { CircleDollarSign } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { formatTokens, formatUsd } from "../lib/cps";
import { modelLabel } from "../lib/pipeline";
import type { Estimate, UsageTotals } from "../types";
import { Spinner, cx } from "./ui";

const STAGE_NAMES = { 1: "Bible", 2: "Translate", 3: "QA critic" } as const;

export function CostBadge({
  estimate,
  usage,
  spent,
  loading,
}: {
  estimate: Estimate | null;
  usage: UsageTotals | null;
  spent: number;
  loading: boolean;
}) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open]);

  if (!estimate && !spent) {
    return (
      <div className="inline-flex h-9 items-center gap-1.5 rounded-lg border border-line bg-panel-2 px-3 text-xs text-muted">
        <CircleDollarSign size={14} /> {loading ? <Spinner /> : "—"}
      </div>
    );
  }

  const unpriced = estimate?.stages.some((s) => !s.priced && !s.skipped);
  return (
    <div className="relative" ref={ref}>
      <button
        type="button"
        onClick={() => setOpen((v) => !v)}
        className="inline-flex h-9 items-center gap-2 rounded-lg border border-line bg-panel-2 px-3 text-xs transition hover:border-line-strong"
        aria-expanded={open}
        title="Cost & token estimate"
      >
        <CircleDollarSign size={14} className="text-ok" />
        {spent > 0 && <span className="font-semibold text-ink tabular-nums">{formatUsd(spent)}</span>}
        {spent > 0 && estimate && <span className="text-muted">/</span>}
        {estimate && (
          <span className={cx("tabular-nums", spent > 0 ? "text-muted" : "font-semibold text-ink")}>
            ≈{formatUsd(estimate.total_cost_usd)}
            {unpriced && "+"}
          </span>
        )}
        {loading && <Spinner className="text-muted" />}
      </button>
      {open && (
        <div className="animate-slide-in absolute right-0 top-11 z-40 w-[340px] rounded-xl border border-line bg-panel p-3 shadow-2xl shadow-black/30">
          <div className="mb-2 flex items-baseline justify-between">
            <span className="text-sm font-semibold text-ink">Cost &amp; tokens</span>
            {estimate && (
              <span className="text-[11px] text-muted">
                {estimate.lines.toLocaleString()} lines · {estimate.batches} batches · {estimate.qa_windows} QA windows
              </span>
            )}
          </div>
          <table className="w-full text-xs">
            <thead>
              <tr className="text-left text-[11px] text-muted">
                <th className="py-1 font-medium">Stage</th>
                <th className="py-1 font-medium">In / cached / out</th>
                <th className="py-1 text-right font-medium">Est.</th>
                <th className="py-1 text-right font-medium">Spent</th>
              </tr>
            </thead>
            <tbody>
              {estimate?.stages.map((s) => {
                const actual = usage ? usage[`stage${s.stage}` as const] : null;
                return (
                  <tr key={s.stage} className="border-t border-line align-top">
                    <td className="py-1.5 pr-2">
                      <div className="font-medium text-ink">{STAGE_NAMES[s.stage]}</div>
                      <div className="text-[10px] text-muted">{modelLabel(s.model)}</div>
                    </td>
                    <td className="py-1.5 tabular-nums text-ink-2">
                      {s.skipped ? (
                        <span className="text-muted">done</span>
                      ) : (
                        <>
                          {formatTokens(s.input_tokens)} / {formatTokens(s.cached_tokens)} / {formatTokens(s.output_tokens)}
                        </>
                      )}
                    </td>
                    <td className="py-1.5 text-right tabular-nums text-ink-2">{s.priced ? formatUsd(s.cost_usd) : "n/a"}</td>
                    <td className="py-1.5 text-right tabular-nums text-ink">{actual ? formatUsd(actual.cost_usd) : "—"}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
          {usage && (
            <div className="mt-2 rounded-lg bg-panel-2 p-2 text-[11px] text-muted">
              Actual: {formatTokens(usage.stage1.input_tokens + usage.stage2.input_tokens + usage.stage3.input_tokens)} in (
              {formatTokens(usage.stage1.cache_hit_tokens + usage.stage2.cache_hit_tokens + usage.stage3.cache_hit_tokens)} cache
              hits) · {formatTokens(usage.stage1.output_tokens + usage.stage2.output_tokens + usage.stage3.output_tokens)} out ·{" "}
              {usage.stage1.calls + usage.stage2.calls + usage.stage3.calls} calls
            </div>
          )}
          <p className="mt-2 text-[10px] leading-relaxed text-muted">
            Estimates use character-based token counts and current list prices (DeepSeek peak hours bill 2×). OpenRouter
            spend is taken from the cost it reports.
          </p>
        </div>
      )}
    </div>
  );
}
