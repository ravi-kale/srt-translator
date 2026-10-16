import { BookOpenText, Clapperboard, FilePlus2, LogOut, Moon, Settings2, Sparkles, Sun, X } from "lucide-react";
import type { ReactNode } from "react";
import type { Estimate, JobDetail } from "../types";
import { CostBadge } from "./CostBadge";
import { JobHistoryMenu } from "./JobHistoryMenu";
import { LanguageSelect } from "./LanguageSelect";
import { StatusBadge } from "./StatusBadge";
import { Button, IconButton, TextInput, cx } from "./ui";

export type PrimaryAction = { label: string; icon: ReactNode; onClick: () => void; disabled?: boolean; variant?: "primary" | "secondary" };

export function TopBar({
  detail,
  targetLanguage,
  onTargetLanguage,
  languageLocked,
  contextHints,
  onContextHints,
  pauseAfterBible,
  onPauseAfterBible,
  qaEnabled,
  onQaEnabled,
  estimate,
  estimating,
  primary,
  keysMissing,
  theme,
  onToggleTheme,
  onOpenSettings,
  onNewFile,
  onHome,
  onOpenJob,
  onJobDeleted,
  onSignOut,
}: {
  detail: JobDetail | null;
  targetLanguage: string;
  onTargetLanguage: (v: string) => void;
  languageLocked: boolean;
  contextHints: string;
  onContextHints: (v: string) => void;
  pauseAfterBible: boolean;
  onPauseAfterBible: (v: boolean) => void;
  qaEnabled: boolean;
  onQaEnabled: (v: boolean) => void;
  estimate: Estimate | null;
  estimating: boolean;
  primary: PrimaryAction | null;
  keysMissing: boolean;
  theme: "dark" | "light";
  onToggleTheme: () => void;
  onOpenSettings: () => void;
  onNewFile: () => void;
  onHome: () => void;
  onOpenJob: (id: string) => void;
  onJobDeleted: (id: string) => void;
  onSignOut?: () => void;
}) {
  const job = detail?.job ?? null;
  return (
    <header className="border-b border-line bg-panel">
      <div className="flex flex-wrap items-center gap-x-3 gap-y-2 px-4 py-2.5">
        <button
          type="button"
          onClick={onHome}
          title="Home: upload a new .srt"
          className="flex items-center gap-2 rounded-lg pr-1 text-left transition hover:opacity-80"
        >
          <span className="flex h-8 w-8 items-center justify-center rounded-lg bg-accent text-accent-ink">
            <Clapperboard size={17} />
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight text-ink">SubTrans Studio</div>
            <div className="text-[10px] text-muted">.srt localization &amp; QA</div>
          </div>
        </button>

        <div className="hidden h-7 w-px bg-line lg:block" />

        {job ? (
          <div className="flex min-w-0 items-center gap-2 rounded-lg border border-line bg-panel-2 py-1 pl-2.5 pr-1">
            <div className="min-w-0">
              <div className="max-w-[220px] truncate text-xs font-medium text-ink" title={job.filename}>
                {job.filename}
              </div>
              <div className="text-[10px] text-muted">{job.block_count.toLocaleString()} lines</div>
            </div>
            <StatusBadge status={job.status} />
            <IconButton label="Upload another .srt" className="h-7 w-7" onClick={onNewFile}>
              <FilePlus2 size={15} />
            </IconButton>
            <IconButton label="Close file and go home" className="h-7 w-7" onClick={onHome}>
              <X size={15} />
            </IconButton>
          </div>
        ) : (
          <Button size="sm" icon={<FilePlus2 size={14} />} onClick={onNewFile}>
            Upload .srt
          </Button>
        )}

        <div className="w-full sm:w-52" title={languageLocked ? "Language is locked once translation starts" : undefined}>
          <LanguageSelect value={targetLanguage} onChange={onTargetLanguage} disabled={languageLocked} />
        </div>

        <div className="min-w-[180px] flex-1">
          <TextInput
            aria-label="Initial context hints"
            placeholder="Context hints (optional): e.g. Mumbai crime drama, Raj speaks to elders with 'Aap'"
            value={contextHints}
            onChange={(e) => onContextHints(e.target.value)}
          />
        </div>

        <button
          type="button"
          onClick={() => onPauseAfterBible(!pauseAfterBible)}
          aria-pressed={pauseAfterBible}
          title="Pause after Stage 1 so you can review the Translation Bible"
          className={cx(
            "inline-flex h-9 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition",
            pauseAfterBible ? "border-accent/50 bg-accent-soft text-accent" : "border-line bg-panel-2 text-muted hover:text-ink",
          )}
        >
          <BookOpenText size={14} /> Review Bible first
        </button>

        <button
          type="button"
          onClick={() => onQaEnabled(!qaEnabled)}
          aria-pressed={qaEnabled}
          title={qaEnabled ? "Stage 3 QA critic runs after translation (click to skip it)" : "Stage 3 QA critic is skipped (click to run it)"}
          className={cx(
            "inline-flex h-9 items-center gap-1.5 rounded-lg border px-2.5 text-xs font-medium transition",
            qaEnabled ? "border-qa/50 bg-qa-soft text-qa" : "border-line bg-panel-2 text-muted hover:text-ink",
          )}
        >
          <Sparkles size={14} /> QA critic {qaEnabled ? "on" : "off"}
        </button>

        <CostBadge estimate={estimate} usage={detail?.usage ?? null} spent={detail?.total_cost_usd ?? 0} loading={estimating} />

        {primary && (
          <Button variant={primary.variant ?? "primary"} icon={primary.icon} onClick={primary.onClick} disabled={primary.disabled}>
            {primary.label}
          </Button>
        )}

        <div className="flex items-center">
          <div className="relative">
            <IconButton label="Models & API keys" onClick={onOpenSettings}>
              <Settings2 size={17} />
            </IconButton>
            {keysMissing && <span className="pointer-events-none absolute right-1.5 top-1.5 h-2 w-2 rounded-full bg-danger ring-2 ring-panel" />}
          </div>
          <JobHistoryMenu currentId={job?.id ?? null} onOpen={onOpenJob} onDeleted={onJobDeleted} />
          <IconButton label={theme === "dark" ? "Switch to light theme" : "Switch to dark theme"} onClick={onToggleTheme}>
            {theme === "dark" ? <Sun size={17} /> : <Moon size={17} />}
          </IconButton>
          {onSignOut && (
            <IconButton label="Sign out" onClick={onSignOut}>
              <LogOut size={17} />
            </IconButton>
          )}
        </div>
      </div>
    </header>
  );
}
