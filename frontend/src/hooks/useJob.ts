import { useCallback, useEffect, useReducer, useRef } from "react";
import { api, ApiError } from "../api";
import type { Bible, Block, JobChanges, JobDetail, JobStatus, JobSummary, LogEntry, Progress, UsageTotals } from "../types";
import { RUNNING } from "../types";

interface JobState {
  detail: JobDetail | null;
  logs: LogEntry[];
  // id -> timestamp of the last streamed update (drives the row flash animation)
  updatedAt: Record<string, number>;
  connected: boolean;
  notFound: boolean;
}

type Action =
  | { type: "clear" }
  | { type: "snapshot"; detail: JobDetail }
  | { type: "status"; job: JobSummary }
  | { type: "progress"; progress: Progress }
  | { type: "bible"; bible: Bible; approved: boolean }
  | { type: "blocks"; blocks: Block[]; flash: boolean }
  | { type: "usage"; usage: UsageTotals; total: number }
  | { type: "log"; entry: LogEntry }
  | { type: "changes"; changes: JobChanges }
  | { type: "connected"; value: boolean }
  | { type: "notFound" };

const initial: JobState = { detail: null, logs: [], updatedAt: {}, connected: false, notFound: false };

function reducer(state: JobState, action: Action): JobState {
  const d = state.detail;
  switch (action.type) {
    case "clear":
      return initial;
    case "notFound":
      return { ...initial, notFound: true };
    case "connected":
      return { ...state, connected: action.value };
    case "snapshot":
      return { ...state, detail: action.detail, logs: action.detail.logs ?? [], notFound: false };
    case "log":
      return { ...state, logs: [...state.logs.slice(-79), action.entry] };
  }
  if (!d) return state;
  switch (action.type) {
    case "changes": {
      const c = action.changes;
      let next = state;
      if (c.blocks.length) next = reducer(next, { type: "blocks", blocks: c.blocks, flash: true });
      if (c.logs.length) {
        const known = new Set(next.logs.map((l) => l.id));
        next = { ...next, logs: [...next.logs, ...c.logs.filter((l) => !known.has(l.id))].slice(-80) };
      }
      const nd = next.detail!;
      // Never let an older summary (e.g. an HTTP response that raced the poll) win.
      const job = c.job.updated_at >= nd.job.updated_at ? c.job : nd.job;
      return {
        ...next,
        detail: { ...nd, job, bible: c.bible, usage: c.usage, total_cost_usd: c.total_cost_usd },
      };
    }
    case "status":
      // HTTP responses and SSE events can arrive out of order; never let an older summary win.
      if (action.job.id !== d.job.id || action.job.updated_at < d.job.updated_at) return state;
      return { ...state, detail: { ...d, job: { ...action.job, progress: action.job.progress ?? d.job.progress } } };
    case "progress":
      return { ...state, detail: { ...d, job: { ...d.job, progress: action.progress } } };
    case "bible":
      return {
        ...state,
        detail: { ...d, bible: action.bible, job: { ...d.job, has_bible: true, bible_approved: action.approved } },
      };
    case "usage":
      return {
        ...state,
        detail: { ...d, usage: action.usage, total_cost_usd: action.total, job: { ...d.job, total_cost_usd: action.total } },
      };
    case "blocks": {
      const blocks = d.blocks.slice();
      const updatedAt = action.flash ? { ...state.updatedAt } : state.updatedAt;
      const now = Date.now();
      for (const b of action.blocks) {
        const pos = Number(b.id) - 1;
        if (blocks[pos]?.id === b.id) {
          blocks[pos] = b;
          if (action.flash) updatedAt[b.id] = now;
        }
      }
      return { ...state, updatedAt, detail: { ...d, blocks } };
    }
  }
  return state;
}

const RUNNING_POLL_MS = 1500;
const IDLE_POLL_MS = 8000;
const MAX_BACKOFF_MS = 15000;

export function useJob(jobId: string | null) {
  const [state, dispatch] = useReducer(reducer, initial);
  // Polling cursors and the latest status, read by the poll loop without re-subscribing.
  const cursor = useRef({ rev: 0, log: 0 });
  const status = useRef<string | null>(null);
  const wake = useRef<() => void>(() => {});
  status.current = state.detail?.job.status ?? null;

  const refresh = useCallback(async () => {
    if (!jobId) return;
    try {
      const detail = await api.getJob(jobId);
      cursor.current = { rev: detail.rev, log: detail.logs.at(-1)?.id ?? 0 };
      dispatch({ type: "snapshot", detail });
      dispatch({ type: "connected", value: true });
    } catch (e) {
      if (e instanceof ApiError && e.status === 404) dispatch({ type: "notFound" });
    }
  }, [jobId]);

  useEffect(() => {
    dispatch({ type: "clear" });
    if (!jobId) return;
    let stopped = false;
    let timer: number | undefined;
    let failures = 0;

    const schedule = (ms: number) => {
      window.clearTimeout(timer);
      if (!stopped) timer = window.setTimeout(tick, ms);
    };
    const tick = async () => {
      try {
        const c = await api.changes(jobId, cursor.current.rev, cursor.current.log);
        if (stopped) return;
        cursor.current = { rev: c.rev, log: c.logs.at(-1)?.id ?? cursor.current.log };
        dispatch({ type: "changes", changes: c });
        dispatch({ type: "connected", value: true });
        failures = 0;
        status.current = c.job.status;
      } catch (e) {
        if (stopped) return;
        if (e instanceof ApiError && e.status === 404) {
          dispatch({ type: "notFound" });
          return;
        }
        dispatch({ type: "connected", value: false });
        failures += 1;
        schedule(Math.min(MAX_BACKOFF_MS, 1000 * 2 ** Math.min(failures, 4)));
        return;
      }
      schedule(status.current && RUNNING.has(status.current as JobStatus) ? RUNNING_POLL_MS : IDLE_POLL_MS);
    };

    wake.current = () => schedule(0);
    void (async () => {
      await refresh();
      schedule(RUNNING_POLL_MS);
    })();
    return () => {
      stopped = true;
      window.clearTimeout(timer);
      wake.current = () => {};
    };
  }, [jobId, refresh]);

  const applyLocal = useCallback((blocks: Block[]) => dispatch({ type: "blocks", blocks, flash: false }), []);
  const applyStatus = useCallback((job: JobSummary) => {
    dispatch({ type: "status", job });
    status.current = job.status;
    wake.current(); // a run just started or paused: poll right away at the faster pace
  }, []);

  return { ...state, refresh, applyLocal, applyStatus };
}
