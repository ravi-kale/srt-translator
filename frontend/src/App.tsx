import { BookOpenText, Download, Loader2, Pause, Play } from "lucide-react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, ApiError, UNAUTHORIZED_EVENT } from "./api";
import { BibleDrawer } from "./components/BibleDrawer";
import { ConfirmProvider, ToastProvider, useConfirm, useToast } from "./components/Feedback";
import { PipelineProgress } from "./components/PipelineProgress";
import { SettingsModal } from "./components/SettingsModal";
import { FilterTabs, SearchBox, SubtitleGrid, isHighCps, type GridFilter } from "./components/SubtitleGrid";
import { TopBar, type PrimaryAction } from "./components/TopBar";
import { LoginPage } from "./components/LoginPage";
import { DropOverlay, EmptyDropzone, isSrt, pickSrtFile, useSrtDrop } from "./components/UploadDropzone";
import { Button } from "./components/ui";
import { useJob } from "./hooks/useJob";
import { useSettings } from "./hooks/useSettings";
import { missingProviders, type Stage } from "./lib/pipeline";
import type { AppConfig, Bible, Estimate, JobDetail, RunRequest } from "./types";
import { RUNNING } from "./types";

const LAST_JOB_KEY = "subtrans.lastJob";
const THEME_KEY = "subtrans.theme";

function initialJobId(): string | null {
  const m = /job=([\w-]+)/.exec(window.location.hash);
  if (m) return m[1];
  try {
    return localStorage.getItem(LAST_JOB_KEY);
  } catch {
    return null;
  }
}

function neededStages(detail: JobDetail): Stage[] {
  const stages: Stage[] = [];
  if (!detail.job.has_bible) stages.push(1);
  if (detail.blocks.some((b) => b.source_text.trim() && !b.user_edited && !b.draft_translation && !b.final_translation)) stages.push(2);
  if (detail.job.status !== "completed") stages.push(3);
  return stages;
}

export default function App() {
  return (
    <ToastProvider>
      <ConfirmProvider>
        <AuthGate />
      </ConfirmProvider>
    </ToastProvider>
  );
}

type AuthState = { status: "checking" } | { status: "login"; notConfigured: boolean } | { status: "in"; required: boolean };

function AuthGate() {
  const [auth, setAuth] = useState<AuthState>({ status: "checking" });
  const check = useCallback(async () => {
    try {
      const s = await api.session();
      setAuth(s.authenticated ? { status: "in", required: s.auth_required } : { status: "login", notConfigured: !s.configured });
    } catch {
      // Backend unreachable: let the studio load and show its own "backend unreachable" toast.
      setAuth({ status: "in", required: false });
    }
  }, []);
  useEffect(() => {
    void check();
    const onExpired = () => setAuth((a) => (a.status === "in" && !a.required ? a : { status: "login", notConfigured: false }));
    window.addEventListener(UNAUTHORIZED_EVENT, onExpired);
    return () => window.removeEventListener(UNAUTHORIZED_EVENT, onExpired);
  }, [check]);

  if (auth.status === "checking")
    return (
      <div className="flex h-full items-center justify-center text-muted">
        <Loader2 size={18} className="animate-spin" />
      </div>
    );
  if (auth.status === "login") return <LoginPage notConfigured={auth.notConfigured} onSignedIn={() => void check()} />;
  return (
    <Studio
      onSignOut={
        auth.required
          ? async () => {
              await api.logout().catch(() => undefined);
              setAuth({ status: "login", notConfigured: false });
            }
          : undefined
      }
    />
  );
}

function Studio({ onSignOut }: { onSignOut?: () => void }) {
  const toast = useToast();
  const confirm = useConfirm();

  // ---------------------------------------------------------------- server config & settings
  const [appConfig, setAppConfig] = useState<AppConfig | null>(null);
  const loadConfig = useCallback(async () => {
    try {
      setAppConfig(await api.config());
    } catch (e) {
      toast({ title: "Backend unreachable", body: (e as Error).message, tone: "error" });
    }
  }, [toast]);
  useEffect(() => {
    void loadConfig();
  }, [loadConfig]);
  const settings = useSettings(appConfig?.defaults ?? null);
  const config = settings.config;

  // ---------------------------------------------------------------- job selection & live state
  const [jobId, setJobId] = useState<string | null>(initialJobId);
  useEffect(() => {
    // Support pasted / bookmarked #job=<id> links and back/forward navigation.
    const onHash = () => setJobId(/job=([\w-]+)/.exec(window.location.hash)?.[1] ?? null);
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    window.history.replaceState(null, "", jobId ? `#job=${jobId}` : window.location.pathname);
    try {
      if (jobId) localStorage.setItem(LAST_JOB_KEY, jobId);
      else localStorage.removeItem(LAST_JOB_KEY);
    } catch {
      /* ignore */
    }
  }, [jobId]);

  const job = useJob(jobId);
  const detail = job.detail;
  const status = detail?.job.status ?? null;
  const running = status !== null && RUNNING.has(status);

  useEffect(() => {
    if (job.notFound) {
      setJobId(null);
      toast({ title: "That job no longer exists", tone: "warning" });
    }
  }, [job.notFound, toast]);

  // Adopt the job's own language/hints when it is opened.
  const adoptedFor = useRef<string | null>(null);
  useEffect(() => {
    if (!detail || adoptedFor.current === detail.job.id) return;
    adoptedFor.current = detail.job.id;
    if (detail.job.target_language) settings.setTargetLanguage(detail.job.target_language);
    settings.setContextHints(detail.job.context_hints);
  }, [detail, settings]);

  const languageLocked = !!detail && (detail.job.has_bible || detail.blocks.some((b) => b.draft_translation || b.final_translation));
  const targetLanguage = languageLocked && detail ? detail.job.target_language : settings.targetLanguage;

  // Toast on live status transitions (not on the initial snapshot).
  const prevStatus = useRef<{ id: string; status: string } | null>(null);
  useEffect(() => {
    if (!detail) return;
    const prev = prevStatus.current;
    prevStatus.current = { id: detail.job.id, status: detail.job.status };
    if (!prev || prev.id !== detail.job.id || prev.status === detail.job.status) return;
    if (detail.job.status === "completed")
      toast({ title: "Translation complete", body: `${detail.job.progress.qa_fixes} QA fixes applied. Ready to export.`, tone: "success" });
    if (detail.job.status === "failed") toast({ title: "Pipeline stopped", body: detail.job.error ?? undefined, tone: "error" });
    if (detail.job.status === "awaiting_review") setDrawerOpen(true);
  }, [detail, toast]);

  // ---------------------------------------------------------------- UI state
  const [theme, setTheme] = useState<"dark" | "light">(() => (document.documentElement.classList.contains("dark") ? "dark" : "light"));
  const toggleTheme = () => {
    const next = theme === "dark" ? "light" : "dark";
    document.documentElement.classList.toggle("dark", next === "dark");
    try {
      localStorage.setItem(THEME_KEY, next);
    } catch {
      /* ignore */
    }
    setTheme(next);
  };
  const [drawerOpen, setDrawerOpen] = useState(false);
  const [settingsOpen, setSettingsOpen] = useState(false);
  const [filter, setFilter] = useState<GridFilter>("all");
  const [search, setSearch] = useState("");
  const [uploading, setUploading] = useState(false);
  const [busy, setBusy] = useState(false);

  // ---------------------------------------------------------------- estimate
  const [estimate, setEstimate] = useState<Estimate | null>(null);
  const [estimating, setEstimating] = useState(false);
  const hasDetail = !!detail;
  const hasBible = !!detail?.job.has_bible;
  useEffect(() => {
    if (!jobId || !hasDetail) {
      setEstimate(null);
      return;
    }
    let cancelled = false;
    const t = window.setTimeout(async () => {
      setEstimating(true);
      try {
        const e = await api.estimate(jobId, { config, target_language: targetLanguage || undefined });
        if (!cancelled) setEstimate(e);
      } catch {
        /* estimate is best-effort */
      } finally {
        if (!cancelled) setEstimating(false);
      }
    }, 350);
    return () => {
      cancelled = true;
      window.clearTimeout(t);
    };
  }, [jobId, hasDetail, hasBible, config, targetLanguage]);

  // ---------------------------------------------------------------- helpers
  const reportError = useCallback(
    (title: string, e: unknown) => {
      if (e instanceof ApiError && e.missingKeys.length) {
        toast({ title: "API key missing", body: `Add ${e.missingKeys.join(", ")} to backend/.env`, tone: "error" });
        setSettingsOpen(true);
        return;
      }
      toast({ title, body: e instanceof Error ? e.message : String(e), tone: "error" });
    },
    [toast],
  );

  const runRequest = (): RunRequest => ({
    config,
    target_language: languageLocked ? undefined : settings.targetLanguage,
    context_hints: settings.contextHints,
  });

  const keysOk = (stages: Stage[]): boolean => {
    const missing = missingProviders(config, appConfig?.keys, stages);
    if (!missing.length) return true;
    toast({
      title: "API key missing",
      body: `Add ${missing.map((p) => appConfig?.key_env_names[p] ?? p).join(", ")} to backend/.env, or switch models in Settings.`,
      tone: "error",
    });
    setSettingsOpen(true);
    return false;
  };

  // ---------------------------------------------------------------- actions
  const handleFile = useCallback(
    async (file: File) => {
      if (!isSrt(file)) {
        toast({ title: "Not an .srt file", body: file.name, tone: "warning" });
        return;
      }
      setUploading(true);
      try {
        const d = await api.upload(file, settings.targetLanguage, settings.contextHints);
        setFilter("all");
        setSearch("");
        setJobId(d.job.id);
        toast({ title: `Parsed ${d.job.block_count.toLocaleString()} subtitle lines`, body: d.job.filename, tone: "success" });
      } catch (e) {
        reportError("Upload failed", e);
      } finally {
        setUploading(false);
      }
    },
    [settings.targetLanguage, settings.contextHints, toast, reportError],
  );
  const dragging = useSrtDrop(handleFile);

  const start = async () => {
    if (!jobId || !detail) return;
    if (!targetLanguage.trim()) {
      toast({ title: "Choose a target language first", tone: "warning" });
      return;
    }
    if (!keysOk(neededStages(detail))) return;
    setBusy(true);
    try {
      job.applyStatus(await api.run(jobId, runRequest()));
    } catch (e) {
      reportError("Could not start", e);
    } finally {
      setBusy(false);
    }
  };

  const pause = async () => {
    if (!jobId) return;
    setBusy(true);
    try {
      await api.pause(jobId);
      toast({ title: "Pausing…", body: "In-flight requests finish first; progress is saved.", tone: "info" });
    } catch (e) {
      reportError("Could not pause", e);
    } finally {
      setBusy(false);
    }
  };

  const saveBible = async (bible: Bible) => {
    if (!jobId) return;
    setBusy(true);
    try {
      await api.putBible(jobId, bible);
      toast({ title: "Translation Bible saved", tone: "success" });
    } catch (e) {
      reportError("Could not save the Bible", e);
    } finally {
      setBusy(false);
    }
  };

  const approveBible = async (bible: Bible) => {
    if (!jobId || !detail) return;
    if (!keysOk([2, 3])) return;
    setBusy(true);
    try {
      await api.putBible(jobId, bible, true);
      job.applyStatus(await api.run(jobId, runRequest()));
    } catch (e) {
      reportError("Could not continue", e);
    } finally {
      setBusy(false);
    }
  };

  const rerun = async (stage: 2 | 3, bible: Bible, dirty: boolean) => {
    if (!jobId) return;
    const ok = await confirm({
      title: stage === 2 ? (config.qa_enabled ? "Re-run Stage 2 + 3?" : "Re-run Stage 2?") : "Re-run the Stage 3 critic?",
      body:
        stage === 2
          ? `All machine translations${config.qa_enabled ? " and QA fixes" : ""} are discarded and regenerated with the current Bible. Lines you edited by hand are kept.`
          : "Current QA fixes are discarded and the critic reviews the DeepSeek drafts again. Lines you edited by hand are kept.",
      confirmLabel: "Re-run",
      tone: "danger",
    });
    if (!ok || !keysOk(stage === 2 ? (config.qa_enabled ? [2, 3] : [2]) : [3])) return;
    setBusy(true);
    try {
      if (dirty) await api.putBible(jobId, bible);
      job.applyStatus(await api.rerun(jobId, stage, runRequest()));
    } catch (e) {
      reportError("Could not re-run", e);
    } finally {
      setBusy(false);
    }
  };

  const exportSrt = async () => {
    if (!jobId || !detail) return;
    const untranslated = detail.blocks.filter((b) => b.source_text.trim() && !(b.final_translation || b.draft_translation)).length;
    if (untranslated) {
      const ok = await confirm({
        title: "Some lines are not translated",
        body: `${untranslated.toLocaleString()} line(s) have no translation yet and will be exported with their source text.`,
        confirmLabel: "Export anyway",
      });
      if (!ok) return;
    }
    try {
      const { filename } = await api.downloadExport(jobId);
      toast({ title: "Exported", body: `${filename} — original timestamps, UTF-8`, tone: "success" });
    } catch (e) {
      reportError("Export failed", e);
    }
  };

  const onCommit = useCallback(
    async (id: string, text: string) => {
      if (!jobId) return;
      try {
        job.applyLocal([await api.editBlock(jobId, id, text)]);
      } catch (e) {
        reportError(`Could not save line #${id}`, e);
        void job.refresh();
      }
    },
    [jobId, job.applyLocal, job.refresh, reportError],
  );

  const onRevert = useCallback(
    async (id: string) => {
      if (!jobId) return;
      try {
        job.applyLocal([await api.revertBlock(jobId, id)]);
      } catch (e) {
        reportError(`Could not revert line #${id}`, e);
      }
    },
    [jobId, job.applyLocal, reportError],
  );

  // ---------------------------------------------------------------- derived
  const threshold = config.cps_threshold;
  const blocks = detail?.blocks ?? [];
  const counts = useMemo(
    () => ({
      all: blocks.length,
      qa: blocks.reduce((n, b) => n + (b.qa_fixed ? 1 : 0), 0),
      cps: blocks.reduce((n, b) => n + (isHighCps(b, threshold) ? 1 : 0), 0),
    }),
    [blocks, threshold],
  );
  const anyTranslated = useMemo(() => blocks.some((b) => b.final_translation || b.draft_translation), [blocks]);
  const keysMissing = missingProviders(config, appConfig?.keys).length > 0;

  let primary: PrimaryAction | null = null;
  if (detail) {
    if (status === "ready") primary = { label: "Start translation", icon: <Play size={15} />, onClick: start, disabled: busy || !targetLanguage.trim() };
    else if (running) primary = { label: "Pause", icon: <Pause size={15} />, onClick: pause, disabled: busy, variant: "secondary" };
    else if (status === "awaiting_review") primary = { label: "Review Bible", icon: <BookOpenText size={15} />, onClick: () => setDrawerOpen(true) };
    else if (status === "paused" || status === "interrupted" || status === "failed")
      primary = { label: "Resume", icon: <Play size={15} />, onClick: start, disabled: busy };
  }

  return (
    <div className="flex h-full flex-col">
      <TopBar
        detail={detail}
        targetLanguage={targetLanguage}
        onTargetLanguage={settings.setTargetLanguage}
        languageLocked={languageLocked}
        contextHints={settings.contextHints}
        onContextHints={settings.setContextHints}
        pauseAfterBible={config.pause_after_bible}
        qaEnabled={config.qa_enabled}
        onQaEnabled={(v) => settings.setConfig({ qa_enabled: v })}
        onPauseAfterBible={(v) => settings.setConfig({ pause_after_bible: v })}
        estimate={estimate}
        estimating={estimating}
        primary={primary}
        keysMissing={keysMissing}
        theme={theme}
        onToggleTheme={toggleTheme}
        onOpenSettings={() => setSettingsOpen(true)}
        onNewFile={async () => {
          const file = await pickSrtFile();
          if (file) void handleFile(file);
        }}
        onHome={() => {
          setDrawerOpen(false);
          setJobId(null);
        }}
        onOpenJob={(id) => {
          setFilter("all");
          setSearch("");
          setJobId(id);
        }}
        onJobDeleted={(id) => id === jobId && setJobId(null)}
        onSignOut={onSignOut}
      />

      {detail && (
        <PipelineProgress
          detail={detail}
          logs={job.logs}
          connected={job.connected}
          qaEnabled={config.qa_enabled}
          onReviewBible={() => setDrawerOpen(true)}
          onResume={start}
        />
      )}

      <main className="flex min-h-0 flex-1">
        <section className="flex min-w-0 flex-1 flex-col">
          {detail ? (
            <>
              <div className="flex flex-wrap items-center gap-2 border-b border-line bg-panel px-4 py-2">
                <FilterTabs value={filter} onChange={setFilter} counts={counts} />
                <div className="ml-auto flex w-full flex-wrap items-center gap-2 sm:w-auto">
                  <SearchBox value={search} onChange={setSearch} />
                  <Button size="sm" className="md:hidden" icon={<BookOpenText size={13} />} onClick={() => setDrawerOpen(true)}>
                    Bible
                  </Button>
                  <Button size="sm" variant="primary" icon={<Download size={13} />} onClick={exportSrt} disabled={!anyTranslated}>
                    Export locked .SRT
                  </Button>
                </div>
              </div>
              <SubtitleGrid
                blocks={blocks}
                filter={filter}
                search={search}
                threshold={threshold}
                updatedAt={job.updatedAt}
                canRevert
                onCommit={onCommit}
                onRevert={onRevert}
              />
            </>
          ) : jobId ? (
            <div className="flex flex-1 items-center justify-center gap-2 text-sm text-muted">
              <Loader2 size={16} className="animate-spin" /> Loading job…
            </div>
          ) : (
            <EmptyDropzone onFile={handleFile} uploading={uploading} />
          )}
        </section>

        {detail && (
          <BibleDrawer
            key={detail.job.id}
            open={drawerOpen}
            onToggle={() => setDrawerOpen((v) => !v)}
            bible={detail.bible}
            status={status}
            approved={detail.job.bible_approved}
            busy={busy}
            onSave={saveBible}
            onApprove={approveBible}
            onRerun={rerun}
            qaEnabled={config.qa_enabled}
          />
        )}
      </main>

      {settingsOpen && (
        <SettingsModal
          appConfig={appConfig}
          config={config}
          onChange={settings.setConfig}
          onReset={settings.resetConfig}
          onRefreshKeys={loadConfig}
          onClose={() => setSettingsOpen(false)}
        />
      )}
      <DropOverlay visible={dragging} />
    </div>
  );
}
