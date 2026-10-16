import { Lock, PencilLine, RotateCcw, Sparkles } from "lucide-react";
import { memo, useCallback, useEffect, useLayoutEffect, useRef, useState } from "react";
import { computeCps, cpsTone, formatDuration } from "../lib/cps";
import type { Block } from "../types";
import { Badge, Button, Spinner, cx } from "./ui";

export const GRID_COLS = "md:grid-cols-[minmax(176px,200px)_minmax(0,1fr)_minmax(0,1.15fr)_minmax(0,0.95fr)]";

const TONE_CLASS = {
  none: "border-line bg-panel-2 text-muted",
  ok: "border-ok/25 bg-ok-soft text-ok",
  warn: "border-warn/30 bg-warn-soft text-warn",
  danger: "border-danger/30 bg-danger-soft text-danger",
} as const;

function splitTimestamp(ts: string): [string, string] {
  const [a, b = ""] = ts.split("-->");
  return [a.trim(), b.trim().split(/\s+/)[0] ?? ""];
}

interface Props {
  block: Block;
  threshold: number;
  flashAt: number | undefined;
  canRevert: boolean;
  onCommit: (id: string, text: string) => Promise<void>;
  onRevert: (id: string) => Promise<void>;
  onFocusChange: (id: string, focused: boolean) => void;
}

export const SubtitleRow = memo(function SubtitleRow({ block, threshold, flashAt, canRevert, onCommit, onRevert, onFocusChange }: Props) {
  const serverText = block.final_translation || block.draft_translation;
  const [text, setText] = useState(serverText);
  const [focused, setFocused] = useState(false);
  const [saving, setSaving] = useState(false);
  const [reverting, setReverting] = useState(false);
  const area = useRef<HTMLTextAreaElement>(null);
  const timer = useRef<number | undefined>(undefined);
  const lastSent = useRef(serverText);

  // Adopt streamed/server updates whenever the user is not actively editing this cell.
  useEffect(() => {
    if (!focused) {
      setText(serverText);
      lastSent.current = serverText;
    }
  }, [serverText, focused]);

  useLayoutEffect(() => {
    const el = area.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${el.scrollHeight}px`;
  }, [text]);

  const commit = useCallback(
    async (value: string) => {
      window.clearTimeout(timer.current);
      const trimmed = value.trim();
      if (!trimmed || trimmed === lastSent.current.trim()) return;
      lastSent.current = trimmed;
      setSaving(true);
      try {
        await onCommit(block.id, trimmed);
      } finally {
        setSaving(false);
      }
    },
    [block.id, onCommit],
  );

  const liveCps = focused ? computeCps(text, block.duration_sec) : block.cps;
  const tone = cpsTone(liveCps, threshold);
  const [start, end] = splitTimestamp(block.timestamp);
  const flash = flashAt !== undefined && !focused && Date.now() - flashAt < 1600;
  const untranslated = !serverText && !!block.source_text.trim();

  return (
    <div className={cx("relative grid grid-cols-1 gap-x-4 gap-y-2 border-b border-line px-4 py-3", GRID_COLS, block.qa_fixed && "bg-qa-soft/40")}>
      {flash && <span key={flashAt} className="row-flash pointer-events-none absolute inset-0" />}

      {/* ID + locked timestamp */}
      <div className="relative flex flex-wrap items-start gap-x-2 gap-y-1 md:flex-col">
        <div className="flex items-center gap-1.5 font-mono text-xs">
          <span className="font-semibold text-ink">#{block.id}</span>
          {block.index !== Number(block.id) && <span className="text-muted">({block.index})</span>}
        </div>
        <div className="flex items-center gap-1 font-mono text-[11px] text-muted" title={`Locked timestamp: ${block.timestamp}`}>
          <Lock size={10} className="shrink-0" />
          <span>
            {start} → {end}
          </span>
        </div>
        <div className="flex gap-1">
          <Badge>{formatDuration(block.duration_sec)}</Badge>
          <span
            className={cx("inline-flex items-center rounded-md border px-1.5 py-px text-[11px] font-medium tabular-nums", TONE_CLASS[tone])}
            title={`${liveCps} characters per second (limit ${threshold})`}
          >
            {liveCps ? `${liveCps} cps` : "— cps"}
          </span>
        </div>
      </div>

      {/* Source */}
      <div className="relative min-w-0 whitespace-pre-wrap break-words text-[13px] leading-relaxed text-ink-2">{block.source_text}</div>

      {/* Translation (inline editable) */}
      <div className="relative min-w-0">
        <textarea
          ref={area}
          rows={1}
          value={text}
          spellCheck
          aria-label={`Translation for line ${block.id}`}
          placeholder={untranslated ? "Not translated yet" : ""}
          onFocus={() => {
            setFocused(true);
            onFocusChange(block.id, true);
          }}
          onBlur={() => {
            setFocused(false);
            onFocusChange(block.id, false);
            void commit(text);
          }}
          onChange={(e) => {
            const v = e.target.value;
            setText(v);
            window.clearTimeout(timer.current);
            timer.current = window.setTimeout(() => void commit(v), 900);
          }}
          onKeyDown={(e) => {
            if (e.key === "Escape") {
              window.clearTimeout(timer.current);
              setText(serverText);
              e.currentTarget.blur();
            } else if (e.key === "Enter" && (e.metaKey || e.ctrlKey)) {
              e.preventDefault();
              e.currentTarget.blur();
            }
          }}
          className={cx(
            "block w-full resize-none overflow-hidden rounded-lg border bg-transparent px-2 py-1.5 text-[13px] leading-relaxed text-ink",
            "border-transparent hover:border-line focus:border-accent focus:bg-panel focus:outline-none focus:ring-2 focus:ring-accent/20",
            "placeholder:italic placeholder:text-muted",
          )}
        />
        {(saving || block.user_edited) && (
          <div className="mt-1 flex items-center gap-1.5 px-2 text-[11px] text-muted">
            {saving ? (
              <>
                <Spinner className="h-2.5 w-2.5" /> saving…
              </>
            ) : (
              <>
                <PencilLine size={11} className="text-accent" /> edited by you
              </>
            )}
          </div>
        )}
      </div>

      {/* QA diff / status */}
      <div className="relative min-w-0 text-xs">
        {block.qa_fixed ? (
          <div className="space-y-1.5">
            <Badge tone="qa">
              <Sparkles size={11} /> QA auto-fixed
            </Badge>
            <div className="text-ink-2">{block.qa_reason}</div>
            {block.draft_translation && (
              <div className="whitespace-pre-wrap break-words text-muted line-through decoration-danger/60" title="DeepSeek draft">
                {block.draft_translation}
              </div>
            )}
          </div>
        ) : (
          <div className="flex flex-wrap gap-1">
            {block.flags.includes("MISSING") && <Badge tone="danger">Missing from model</Badge>}
            {block.flags.includes("EMPTY") && !block.flags.includes("MISSING") && <Badge tone="danger">Empty</Badge>}
            {block.flags.includes("IDENTICAL") && <Badge tone="warn">Same as source</Badge>}
            {block.flags.includes("HIGH_CPS") && <Badge tone="warn">⚠ High CPS</Badge>}
            {block.flags.length === 0 && serverText && <span className="text-muted">—</span>}
          </div>
        )}
        {canRevert && (block.qa_fixed || block.user_edited) && block.draft_translation && (
          <Button
            size="sm"
            variant="ghost"
            className="-ml-2 mt-1.5"
            icon={reverting ? <Spinner className="h-3 w-3" /> : <RotateCcw size={12} />}
            disabled={reverting}
            onClick={async () => {
              setReverting(true);
              try {
                await onRevert(block.id);
              } finally {
                setReverting(false);
              }
            }}
          >
            Revert to draft
          </Button>
        )}
      </div>
    </div>
  );
});
