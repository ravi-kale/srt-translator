import { History, Trash2 } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { api } from "../api";
import { formatUsd } from "../lib/cps";
import type { JobSummary } from "../types";
import { useConfirm, useToast } from "./Feedback";
import { StatusBadge } from "./StatusBadge";
import { IconButton, Spinner, cx } from "./ui";

export function JobHistoryMenu({
  currentId,
  onOpen,
  onDeleted,
}: {
  currentId: string | null;
  onOpen: (id: string) => void;
  onDeleted: (id: string) => void;
}) {
  const [open, setOpen] = useState(false);
  const [jobs, setJobs] = useState<JobSummary[] | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  const confirm = useConfirm();
  const toast = useToast();

  useEffect(() => {
    if (!open) return;
    setJobs(null);
    api.listJobs().then(setJobs, (e) => {
      toast({ title: "Could not load jobs", body: String(e.message ?? e), tone: "error" });
      setJobs([]);
    });
    const close = (e: MouseEvent) => !ref.current?.contains(e.target as Node) && setOpen(false);
    window.addEventListener("mousedown", close);
    return () => window.removeEventListener("mousedown", close);
  }, [open, toast]);

  return (
    <div className="relative" ref={ref}>
      <IconButton label="Job history" onClick={() => setOpen((v) => !v)} aria-expanded={open}>
        <History size={17} />
      </IconButton>
      {open && (
        <div className="animate-slide-in absolute right-0 top-11 z-40 w-[min(420px,calc(100vw-2rem))] rounded-xl border border-line bg-panel shadow-2xl shadow-black/30">
          <div className="border-b border-line px-3 py-2 text-sm font-semibold text-ink">Saved jobs</div>
          <div className="max-h-[60vh] overflow-y-auto p-1.5">
            {jobs === null && (
              <div className="flex justify-center p-6 text-muted">
                <Spinner />
              </div>
            )}
            {jobs?.length === 0 && <div className="p-6 text-center text-sm text-muted">No jobs yet.</div>}
            {jobs?.map((j) => (
              <div
                key={j.id}
                className={cx(
                  "group flex cursor-pointer items-center gap-3 rounded-lg px-2.5 py-2 hover:bg-panel-2",
                  j.id === currentId && "bg-accent-soft",
                )}
                onClick={() => {
                  onOpen(j.id);
                  setOpen(false);
                }}
              >
                <div className="min-w-0 flex-1">
                  <div className="truncate text-sm font-medium text-ink">{j.filename}</div>
                  <div className="mt-0.5 flex flex-wrap items-center gap-x-2 text-[11px] text-muted">
                    <span>{j.target_language || "no language"}</span>
                    <span>{j.block_count.toLocaleString()} lines</span>
                    <span>{formatUsd(j.total_cost_usd)}</span>
                    <span>{new Date(j.updated_at * 1000).toLocaleString()}</span>
                  </div>
                </div>
                <StatusBadge status={j.status} />
                <button
                  type="button"
                  aria-label={`Delete ${j.filename}`}
                  className="rounded-md p-1 text-muted opacity-0 transition hover:bg-danger-soft hover:text-danger group-hover:opacity-100 focus:opacity-100"
                  onClick={async (e) => {
                    e.stopPropagation();
                    const ok = await confirm({
                      title: "Delete this job?",
                      body: (
                        <>
                          <span className="font-medium text-ink">{j.filename}</span> and all its translations will be
                          permanently removed from the local database.
                        </>
                      ),
                      confirmLabel: "Delete",
                      tone: "danger",
                    });
                    if (!ok) return;
                    try {
                      await api.deleteJob(j.id);
                      setJobs((js) => js?.filter((x) => x.id !== j.id) ?? null);
                      onDeleted(j.id);
                    } catch (err) {
                      toast({ title: "Delete failed", body: String((err as Error).message), tone: "error" });
                    }
                  }}
                >
                  <Trash2 size={14} />
                </button>
              </div>
            ))}
          </div>
        </div>
      )}
    </div>
  );
}
