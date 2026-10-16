import { AlertTriangle, BookOpenText, Check, Languages, PauseCircle, Play, Sparkles, Wifi, WifiOff } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { formatElapsed } from "../lib/cps";
import type { JobDetail, LogEntry } from "../types";
import { RUNNING } from "../types";
import { Button, Spinner, cx } from "./ui";

type StepState = "pending" | "active" | "done" | "error" | "paused" | "skipped";

function Step({ icon, title, detail, state }: { icon: ReactNode; title: string; detail: string; state: StepState }) {
  return (
    <div className="flex min-w-0 items-center gap-2.5">
      <span
        className={cx(
          "flex h-8 w-8 shrink-0 items-center justify-center rounded-full border",
          state === "done" && "border-ok/40 bg-ok-soft text-ok",
          state === "active" && "border-accent/50 bg-accent-soft text-accent",
          state === "paused" && "border-warn/40 bg-warn-soft text-warn",
          state === "error" && "border-danger/40 bg-danger-soft text-danger",
          (state === "pending" || state === "skipped") && "border-line bg-panel-2 text-muted",
          state === "skipped" && "border-dashed opacity-70",
        )}
      >
        {state === "done" ? <Check size={15} /> : state === "active" ? <Spinner /> : icon}
      </span>
      <div className="min-w-0">
        <div className={cx("text-xs font-semibold", state === "pending" || state === "skipped" ? "text-muted" : "text-ink")}>{title}</div>
        <div className="truncate text-[11px] text-muted tabular-nums">{detail}</div>
      </div>
    </div>
  );
}

function useNow(active: boolean) {
  const [now, setNow] = useState(() => Date.now());
  useEffect(() => {
    if (!active) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [active]);
  return now;
}

export function PipelineProgress({
  detail,
  logs,
  connected,
  qaEnabled,
  onReviewBible,
  onResume,
}: {
  detail: JobDetail;
  logs: LogEntry[];
  connected: boolean;
  qaEnabled: boolean;
  onReviewBible: () => void;
  onResume: () => void;
}) {
  const { job } = detail;
  const p = job.progress;
  const status = job.status;
  const running = RUNNING.has(status);
  const now = useNow(running);
  const halted = status === "paused" || status === "interrupted";

  const s2Total = p.batches_total;
  const s2Done = Math.min(p.batches_done, s2Total);
  const s3Total = p.qa_windows_total;
  const s3Done = new Set(p.qa_windows_done).size;
  const s2Complete = s2Total > 0 && s2Done === s2Total && p.stage >= 3;
  const s3Complete = status === "completed";
  // Running/finished: show what that run did. Otherwise: what the next run will do (current settings).
  const qaOff = running || status === "completed" ? detail.run_config?.qa_enabled === false : !qaEnabled;

  const s1: StepState = job.has_bible
    ? "done"
    : status === "bible_running"
      ? "active"
      : status === "failed" && p.stage <= 1
        ? "error"
        : halted && p.stage === 1
          ? "paused"
          : "pending";
  const s2: StepState = s2Complete
    ? "done"
    : status === "translating"
      ? "active"
      : status === "failed" && p.stage === 2
        ? "error"
        : halted && p.stage === 2
          ? "paused"
          : "pending";
  const s3: StepState = qaOff
    ? "skipped"
    : s3Complete
    ? "done"
    : status === "qa_running"
      ? "active"
      : status === "failed" && p.stage === 3
        ? "error"
        : halted && p.stage === 3
          ? "paused"
          : "pending";

  const pct = s3Complete
    ? 100
    : Math.round(
        (job.has_bible ? 10 : 0) +
          (s2Total ? ((qaOff ? 90 : 70) * s2Done) / s2Total : 0) +
          (s3Total && !qaOff ? (20 * s3Done) / s3Total : 0),
      );

  const elapsed = p.started_at ? ((p.finished_at ?? (running ? now / 1000 : p.started_at)) - p.started_at) : 0;
  const lastLog = logs[logs.length - 1]?.message || p.last_message;

  return (
    <div className="border-b border-line bg-panel">
      <div className="flex flex-wrap items-center gap-x-6 gap-y-3 px-4 py-3">
        <div className="grid min-w-0 flex-1 grid-cols-1 gap-3 sm:grid-cols-3">
          <Step
            icon={<BookOpenText size={15} />}
            title="Stage 1 · Translation Bible"
            state={s1}
            detail={
              job.has_bible
                ? `${detail.bible?.glossary.length ?? 0} terms · ${detail.bible?.speaker_pronoun_rules.length ?? 0} rules${job.bible_approved ? "" : " · awaiting review"}`
                : status === "bible_running"
                  ? "Reading the transcript…"
                  : "Tone, formality & glossary"
            }
          />
          <Step
            icon={<Languages size={15} />}
            title="Stage 2 · DeepSeek translation"
            state={s2}
            detail={s2Total ? `Batch ${s2Done}/${s2Total}` : "Sliding-window batches"}
          />
          <Step
            icon={<Sparkles size={15} />}
            title="Stage 3 · QA critic"
            state={s3}
            detail={
              qaOff ? "Skipped · off in Settings" : s3Total ? `Window ${s3Done}/${s3Total} · ${p.qa_fixes} fixes` : "Sparse patch review"
            }
          />
        </div>
        <div className="flex items-center gap-3 text-xs text-muted">
          <span className="tabular-nums">{pct}%</span>
          {elapsed > 0 && <span className="tabular-nums">{formatElapsed(elapsed)}</span>}
          <span title={connected ? "Live updates connected" : "Reconnecting to live updates…"}>
            {connected ? <Wifi size={14} className="text-ok" /> : <WifiOff size={14} className="text-warn" />}
          </span>
        </div>
      </div>

      <div className="relative h-1 overflow-hidden bg-panel-3">
        <div
          className={cx("h-full transition-[width] duration-500", status === "failed" ? "bg-danger" : s3Complete ? "bg-ok" : "bg-accent")}
          style={{ width: `${pct}%` }}
        />
        {status === "bible_running" && <div className="bar-indeterminate absolute inset-y-0 w-1/3 bg-accent/60" />}
      </div>

      {status === "awaiting_review" && (
        <Banner tone="warn" icon={<BookOpenText size={15} />}>
          <span>
            <b>Translation Bible ready.</b> Review tone, pronoun rules and glossary before Stage 2 starts.
          </span>
          <Button size="sm" variant="primary" onClick={onReviewBible}>
            Review Bible
          </Button>
        </Banner>
      )}
      {status === "failed" && job.error && (
        <Banner tone="danger" icon={<AlertTriangle size={15} />}>
          <span className="min-w-0 break-words">{job.error}</span>
          <Button size="sm" variant="danger" icon={<Play size={13} />} onClick={onResume}>
            Retry
          </Button>
        </Banner>
      )}
      {halted && (
        <Banner tone="warn" icon={<PauseCircle size={15} />}>
          <span>
            {status === "interrupted"
              ? "This job was interrupted (server restart). Progress is saved — resume to continue."
              : "Paused. Completed batches are saved; resume continues where it stopped."}
          </span>
          <Button size="sm" variant="primary" icon={<Play size={13} />} onClick={onResume}>
            Resume
          </Button>
        </Banner>
      )}
      {running && lastLog && (
        <div className="truncate border-t border-line bg-panel-2/40 px-4 py-1.5 font-mono text-[11px] text-muted" title={lastLog}>
          {lastLog}
        </div>
      )}
    </div>
  );
}

function Banner({ tone, icon, children }: { tone: "warn" | "danger"; icon: ReactNode; children: ReactNode }) {
  return (
    <div
      className={cx(
        "flex items-center gap-3 border-t px-4 py-2 text-xs",
        tone === "warn" ? "border-warn/25 bg-warn-soft text-ink" : "border-danger/25 bg-danger-soft text-ink",
      )}
    >
      <span className={tone === "warn" ? "text-warn" : "text-danger"}>{icon}</span>
      <div className="flex min-w-0 flex-1 flex-wrap items-center justify-between gap-2">{children}</div>
    </div>
  );
}
