import { CheckCircle2, ChevronDown, KeyRound, RefreshCw, RotateCcw, X, XCircle } from "lucide-react";
import { useState, type ReactNode } from "react";
import { PROVIDER_LABEL, modelLabel, stageModel, stageProviders, viaOpenRouter, type Stage } from "../lib/pipeline";
import type { AppConfig, PipelineMode, ProviderKey, RunConfig } from "../types";
import { Modal } from "./Feedback";
import { Button, Field, IconButton, NumberInput, Select, TextInput, Toggle, cx } from "./ui";

const MODES: { value: PipelineMode; label: string; hint: string }[] = [
  { value: "hybrid", label: "Hybrid (Combo B)", hint: "Gemini/DeepSeek → DeepSeek → Claude/Gemini" },
  { value: "all_deepseek", label: "All DeepSeek", hint: "One DeepSeek key for every stage" },
  { value: "all_openrouter", label: "All OpenRouter", hint: "One OpenRouter key for every stage" },
];

const STAGE_TITLES: Record<Stage, string> = { 1: "Bible", 2: "Translate", 3: "QA critic" };

function Section({ title, children, aside }: { title: string; children: ReactNode; aside?: ReactNode }) {
  return (
    <section className="border-t border-line px-5 py-4 first:border-t-0">
      <div className="mb-3 flex items-center justify-between">
        <h3 className="text-xs font-semibold uppercase tracking-wider text-muted">{title}</h3>
        {aside}
      </div>
      {children}
    </section>
  );
}

export function SettingsModal({
  appConfig,
  config,
  onChange,
  onReset,
  onRefreshKeys,
  onClose,
}: {
  appConfig: AppConfig | null;
  config: RunConfig;
  onChange: (patch: Partial<RunConfig>) => void;
  onReset: () => void;
  onRefreshKeys: () => Promise<void>;
  onClose: () => void;
}) {
  const [advanced, setAdvanced] = useState(false);
  const [refreshing, setRefreshing] = useState(false);
  const keys = appConfig?.keys;
  const providers = stageProviders(config, keys);
  const required = new Set(Object.values(providers));
  // Direct providers whose missing key is covered by routing their stage through OpenRouter.
  const covered = new Set(
    ([1, 2, 3] as Stage[]).filter((s) => viaOpenRouter(config, s, keys)).map((s) => stageProviders(config)[s]),
  );

  return (
    <Modal onClose={onClose} labelledBy="settings-title" width="max-w-3xl">
      <div className="flex items-center justify-between border-b border-line px-5 py-3.5">
        <div>
          <h2 id="settings-title" className="text-base font-semibold text-ink">
            Models &amp; API keys
          </h2>
          <p className="text-xs text-muted">Keys are read only from backend/.env — they never pass through the browser.</p>
        </div>
        <IconButton label="Close settings" onClick={onClose}>
          <X size={17} />
        </IconButton>
      </div>

      <div className="max-h-[calc(100vh-11rem)] overflow-y-auto">
        <Section
          title="API keys (server-side)"
          aside={
            <Button
              size="sm"
              variant="ghost"
              icon={<RefreshCw size={13} className={cx(refreshing && "animate-spin")} />}
              onClick={async () => {
                setRefreshing(true);
                await onRefreshKeys().finally(() => setRefreshing(false));
              }}
            >
              Re-check .env
            </Button>
          }
        >
          <div className="grid gap-2 sm:grid-cols-2">
            {(Object.keys(PROVIDER_LABEL) as ProviderKey[]).map((p) => {
              const ok = keys?.[p];
              const needed = required.has(p);
              return (
                <div
                  key={p}
                  className={cx(
                    "flex items-center gap-3 rounded-lg border px-3 py-2",
                    needed && !ok ? "border-danger/40 bg-danger-soft" : "border-line bg-panel-2",
                  )}
                >
                  <KeyRound size={15} className="shrink-0 text-muted" />
                  <div className="min-w-0 flex-1">
                    <div className="text-sm font-medium text-ink">
                      {PROVIDER_LABEL[p]} {needed && <span className="text-[10px] font-normal text-muted">· needed</span>}
                    </div>
                    <div className="truncate font-mono text-[11px] text-muted">{appConfig?.key_env_names[p] ?? ""}</div>
                  </div>
                  {ok ? (
                    <span className="inline-flex items-center gap-1 text-xs text-ok">
                      <CheckCircle2 size={14} /> found
                    </span>
                  ) : covered.has(p) ? (
                    <span className="inline-flex items-center gap-1 text-xs text-muted" title="This stage runs on the same model through OpenRouter">
                      <CheckCircle2 size={14} /> via OpenRouter
                    </span>
                  ) : (
                    <span className={cx("inline-flex items-center gap-1 text-xs", needed ? "text-danger" : "text-muted")}>
                      <XCircle size={14} /> missing
                    </span>
                  )}
                </div>
              );
            })}
          </div>
          <p className="mt-2 text-[11px] text-muted">
            Add missing keys to <span className="font-mono text-ink-2">backend/.env</span> (see{" "}
            <span className="font-mono text-ink-2">.env.example</span>), then click Re-check — no restart needed. With an
            OpenRouter key, any stage whose own key is missing runs on the same model through OpenRouter.
          </p>
        </Section>

        <Section title="Pipeline">
          <div className="grid gap-2 sm:grid-cols-3">
            {MODES.map((m) => (
              <button
                key={m.value}
                type="button"
                onClick={() => onChange({ mode: m.value })}
                className={cx(
                  "rounded-lg border px-3 py-2.5 text-left transition",
                  config.mode === m.value ? "border-accent bg-accent-soft" : "border-line bg-panel-2 hover:border-line-strong",
                )}
              >
                <div className="text-sm font-medium text-ink">{m.label}</div>
                <div className="mt-0.5 text-[11px] text-muted">{m.hint}</div>
              </button>
            ))}
          </div>

          {config.mode === "hybrid" && (
            <div className="mt-3 grid gap-3 sm:grid-cols-3">
              <Field label="Stage 1 · Bible extractor">
                <Select
                  value={config.stage1_provider}
                  onChange={(e) => onChange({ stage1_provider: e.target.value as RunConfig["stage1_provider"] })}
                >
                  <option value="gemini">Gemini 4 Argon (reasoning: low)</option>
                  <option value="deepseek">DeepSeek V4.1 Flash</option>
                </Select>
              </Field>
              <Field label="Stage 2 · Translator" hint="Combo B always translates with DeepSeek">
                <Select value="deepseek" disabled>
                  <option value="deepseek">DeepSeek V4.1 Flash</option>
                </Select>
              </Field>
              <Field label="Stage 3 · QA critic">
                <Select
                  value={config.stage3_provider}
                  onChange={(e) => onChange({ stage3_provider: e.target.value as RunConfig["stage3_provider"] })}
                >
                  <option value="claude">Claude Sonnet 5.5</option>
                  <option value="gemini">Gemini 4 Argon</option>
                </Select>
              </Field>
            </div>
          )}

          <div className="mt-3 flex flex-wrap gap-2">
            {([1, 2, 3] as Stage[]).map((s) => {
              const p = providers[s];
              const ok = keys?.[p];
              return (
                <span
                  key={s}
                  className={cx(
                    "inline-flex items-center gap-1.5 rounded-md border px-2 py-1 text-[11px]",
                    ok ? "border-line bg-panel-2 text-ink-2" : "border-danger/40 bg-danger-soft text-danger",
                  )}
                >
                  <span className="font-semibold">S{s}</span> {STAGE_TITLES[s]} → {modelLabel(stageModel(config, s, keys))}
                  <span className="text-muted">({viaOpenRouter(config, s, keys) ? "via OpenRouter" : PROVIDER_LABEL[p]})</span>
                </span>
              );
            })}
          </div>
        </Section>

        <Section title="Stage 2 · Translation">
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="Parallel lanes" hint="1 = strictly sequential">
              <NumberInput value={config.lanes} min={1} max={16} onChange={(v) => onChange({ lanes: v })} />
            </Field>
            <Field label="Batch target">
              <NumberInput
                value={config.target_size}
                min={5}
                max={100}
                onChange={(v) => onChange({ target_size: v, max_size: Math.max(v, config.max_size) })}
              />
            </Field>
            <Field label="Batch max">
              <NumberInput value={config.max_size} min={config.target_size} max={150} onChange={(v) => onChange({ max_size: v })} />
            </Field>
            <Field label="Retry chunk" hint="after an ID mismatch">
              <NumberInput value={config.retry_chunk_size} min={3} max={60} onChange={(v) => onChange({ retry_chunk_size: v })} />
            </Field>
            <Field label="Scene gap (s)" hint="silence that allows a cut">
              <NumberInput value={config.gap_sec} min={0} max={30} step={0.1} onChange={(v) => onChange({ gap_sec: v })} />
            </Field>
            <Field label="Context lines" hint="before & after each batch">
              <NumberInput value={config.context_window} min={0} max={30} onChange={(v) => onChange({ context_window: v })} />
            </Field>
            <Field label="Temperature">
              <NumberInput
                value={config.stage2_temperature}
                min={0}
                max={2}
                step={0.1}
                onChange={(v) => onChange({ stage2_temperature: v })}
              />
            </Field>
            <Field label="Bible sample lines">
              <NumberInput value={config.bible_max_lines} min={50} max={5000} onChange={(v) => onChange({ bible_max_lines: v })} />
            </Field>
          </div>
          <div className="mt-4 grid gap-3 sm:grid-cols-2">
            <Toggle
              checked={config.deepseek_send_thinking_flag}
              onChange={(v) => onChange({ deepseek_send_thinking_flag: v })}
              label={<span className="font-mono text-xs">thinking: {"{type: disabled}"}</span>}
              description="Official api.deepseek.com switch that turns thinking off."
            />
            <Toggle
              checked={config.deepseek_send_chat_template_flag}
              onChange={(v) => onChange({ deepseek_send_chat_template_flag: v })}
              label={<span className="font-mono text-xs">chat_template_kwargs.enable_thinking: false</span>}
              description="vLLM / SGLang flag from the spec. Turn off if the API rejects it."
            />
          </div>
        </Section>

        <Section title="Stage 3 · QA critic">
          <div className="grid gap-3 sm:grid-cols-4">
            <Field label="CPS limit" hint="chars / second">
              <NumberInput value={config.cps_threshold} min={5} max={60} onChange={(v) => onChange({ cps_threshold: v })} />
            </Field>
            <Field label="Window size">
              <NumberInput value={config.qa_window} min={10} max={400} onChange={(v) => onChange({ qa_window: v })} />
            </Field>
            <Field label="Concurrency">
              <NumberInput value={config.qa_concurrency} min={1} max={10} onChange={(v) => onChange({ qa_concurrency: v })} />
            </Field>
            <Field label="Critic effort">
              <Select value={config.qa_effort} onChange={(e) => onChange({ qa_effort: e.target.value as RunConfig["qa_effort"] })}>
                <option value="low">Low</option>
                <option value="medium">Medium</option>
                <option value="high">High</option>
              </Select>
            </Field>
          </div>
          <div className="mt-4">
            <Toggle
              checked={config.claude_refusal_fallback}
              onChange={(v) => onChange({ claude_refusal_fallback: v })}
              label="Claude server-side refusal fallback"
              description='If Claude declines a window, the API retries it on a fallback model (fallbacks: "default").'
            />
          </div>
        </Section>

        <Section
          title="Model IDs"
          aside={
            <button type="button" className="inline-flex items-center gap-1 text-xs text-muted hover:text-ink" onClick={() => setAdvanced((v) => !v)}>
              {advanced ? "Hide" : "Edit"} <ChevronDown size={13} className={cx("transition", advanced && "rotate-180")} />
            </button>
          }
        >
          {advanced ? (
            <div className="grid gap-3 sm:grid-cols-3">
              <Field label="DeepSeek">
                <TextInput value={config.deepseek_model} onChange={(e) => onChange({ deepseek_model: e.target.value })} />
              </Field>
              <Field label="Gemini">
                <TextInput value={config.gemini_model} onChange={(e) => onChange({ gemini_model: e.target.value })} />
              </Field>
              <Field label="Claude">
                <TextInput value={config.claude_model} onChange={(e) => onChange({ claude_model: e.target.value })} />
              </Field>
              <Field label="OpenRouter · Stage 1 / Gemini">
                <TextInput value={config.openrouter_stage1_model} onChange={(e) => onChange({ openrouter_stage1_model: e.target.value })} />
              </Field>
              <Field label="OpenRouter · Stage 2 / DeepSeek">
                <TextInput value={config.openrouter_stage2_model} onChange={(e) => onChange({ openrouter_stage2_model: e.target.value })} />
              </Field>
              <Field label="OpenRouter · Stage 3 / Claude">
                <TextInput value={config.openrouter_stage3_model} onChange={(e) => onChange({ openrouter_stage3_model: e.target.value })} />
              </Field>
              <Field label="Stage 1 reasoning">
                <Select
                  value={config.stage1_reasoning_effort}
                  onChange={(e) => onChange({ stage1_reasoning_effort: e.target.value as RunConfig["stage1_reasoning_effort"] })}
                >
                  <option value="minimal">Minimal</option>
                  <option value="low">Low</option>
                  <option value="medium">Medium</option>
                  <option value="high">High</option>
                </Select>
              </Field>
            </div>
          ) : (
            <p className="text-xs text-muted">
              {config.deepseek_model} · {config.gemini_model} · {config.claude_model}
              {appConfig && <> · base URLs set in backend/.env</>}
            </p>
          )}
        </Section>
      </div>

      <div className="flex items-center justify-between border-t border-line bg-panel-2/50 px-5 py-3">
        <Button variant="ghost" icon={<RotateCcw size={14} />} onClick={onReset}>
          Reset to defaults
        </Button>
        <Button variant="primary" onClick={onClose}>
          Done
        </Button>
      </div>
    </Modal>
  );
}
