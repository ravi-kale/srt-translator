import { useVirtualizer } from "@tanstack/react-virtual";
import { Search, X } from "lucide-react";
import { useCallback, useEffect, useLayoutEffect, useMemo, useRef, useState } from "react";
import type { Block } from "../types";
import { GRID_COLS, SubtitleRow } from "./SubtitleRow";
import { TextInput, cx } from "./ui";

export type GridFilter = "all" | "qa" | "cps";

export function isHighCps(b: Block, threshold: number) {
  return b.flags.includes("HIGH_CPS") || b.cps > threshold;
}

export function FilterTabs({
  value,
  onChange,
  counts,
}: {
  value: GridFilter;
  onChange: (f: GridFilter) => void;
  counts: Record<GridFilter, number>;
}) {
  const tabs: { key: GridFilter; label: string }[] = [
    { key: "all", label: "All lines" },
    { key: "qa", label: "✨ QA auto-fixed" },
    { key: "cps", label: "⚠️ High CPS" },
  ];
  return (
    <div role="tablist" className="flex gap-1 overflow-x-auto rounded-lg bg-panel-2 p-1">
      {tabs.map((t) => (
        <button
          key={t.key}
          role="tab"
          type="button"
          aria-selected={value === t.key}
          onClick={() => onChange(t.key)}
          className={cx(
            "inline-flex items-center gap-1.5 whitespace-nowrap rounded-md px-2.5 py-1 text-xs font-medium transition",
            value === t.key ? "bg-panel text-ink shadow-sm" : "text-muted hover:text-ink",
          )}
        >
          {t.label}
          <span className={cx("rounded px-1 tabular-nums", value === t.key ? "bg-panel-3 text-ink-2" : "text-muted")}>
            {counts[t.key].toLocaleString()}
          </span>
        </button>
      ))}
    </div>
  );
}

export function SearchBox({ value, onChange }: { value: string; onChange: (v: string) => void }) {
  return (
    <div className="relative w-full sm:w-64">
      <Search size={14} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
      <TextInput
        aria-label="Search lines"
        className="h-8 pl-8 pr-7 text-xs"
        placeholder="Search text or #ID"
        value={value}
        onChange={(e) => onChange(e.target.value)}
      />
      {value && (
        <button type="button" aria-label="Clear search" className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-ink" onClick={() => onChange("")}>
          <X size={13} />
        </button>
      )}
    </div>
  );
}

export function SubtitleGrid({
  blocks,
  filter,
  search,
  threshold,
  updatedAt,
  canRevert,
  onCommit,
  onRevert,
}: {
  blocks: Block[];
  filter: GridFilter;
  search: string;
  threshold: number;
  updatedAt: Record<string, number>;
  canRevert: boolean;
  onCommit: (id: string, text: string) => Promise<void>;
  onRevert: (id: string) => Promise<void>;
}) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const headerRef = useRef<HTMLDivElement>(null);
  const [headerHeight, setHeaderHeight] = useState(0);
  // Keep the row being edited visible even if an edit moves it out of the active filter.
  const [pinnedId, setPinnedId] = useState<string | null>(null);

  const rows = useMemo(() => {
    const q = search.trim().toLowerCase();
    const idQuery = /^#?\d+$/.test(q) ? q.replace("#", "") : null;
    return blocks.filter((b) => {
      if (b.id === pinnedId) return true;
      if (filter === "qa" && !b.qa_fixed) return false;
      if (filter === "cps" && !isHighCps(b, threshold)) return false;
      if (!q) return true;
      if (idQuery) return b.id === idQuery || String(b.index) === idQuery;
      return (
        b.source_text.toLowerCase().includes(q) ||
        b.final_translation.toLowerCase().includes(q) ||
        b.draft_translation.toLowerCase().includes(q)
      );
    });
  }, [blocks, filter, search, threshold, pinnedId]);

  useLayoutEffect(() => {
    if (headerRef.current) setHeaderHeight(headerRef.current.offsetHeight);
  }, []);

  // A new view starts at the top instead of keeping the previous view's scroll offset.
  useEffect(() => {
    scrollRef.current?.scrollTo({ top: 0 });
  }, [filter, search]);

  const virtualizer = useVirtualizer({
    count: rows.length,
    getScrollElement: () => scrollRef.current,
    estimateSize: () => 92,
    overscan: 10,
    getItemKey: (i) => rows[i].id,
    scrollMargin: headerHeight,
  });

  const onFocusChange = useCallback((id: string, focused: boolean) => {
    setPinnedId((cur) => (focused ? id : cur === id ? null : cur));
  }, []);

  const items = virtualizer.getVirtualItems();

  return (
    <div ref={scrollRef} className="min-h-0 flex-1 overflow-auto [scrollbar-gutter:stable]">
      <div
        ref={headerRef}
        className={cx(
          "sticky top-0 z-10 hidden gap-x-4 border-b border-line bg-panel/95 px-4 py-2 text-[11px] font-semibold uppercase tracking-wider text-muted backdrop-blur md:grid",
          GRID_COLS,
        )}
      >
        <div>#ID · locked timestamp</div>
        <div>Source text</div>
        <div>Translated text (editable)</div>
        <div>Stage 3 QA diff / status</div>
      </div>
      {rows.length === 0 ? (
        <div className="p-10 text-center text-sm text-muted">No lines match this view.</div>
      ) : (
        <div className="relative w-full" style={{ height: virtualizer.getTotalSize() }}>
          {items.map((item) => {
            const block = rows[item.index];
            return (
              <div
                key={item.key}
                data-index={item.index}
                ref={virtualizer.measureElement}
                className="absolute left-0 top-0 w-full"
                style={{ transform: `translateY(${item.start - virtualizer.options.scrollMargin}px)` }}
              >
                <SubtitleRow
                  block={block}
                  threshold={threshold}
                  flashAt={updatedAt[block.id]}
                  canRevert={canRevert}
                  onCommit={onCommit}
                  onRevert={onRevert}
                  onFocusChange={onFocusChange}
                />
              </div>
            );
          })}
        </div>
      )}
    </div>
  );
}
