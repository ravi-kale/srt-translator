import type { ProviderKey, RunConfig } from "../types";

export type Stage = 1 | 2 | 3;

type Keys = Partial<Record<ProviderKey, boolean>>;

function directProviders(c: RunConfig): Record<Stage, ProviderKey> {
  if (c.mode === "all_deepseek") return { 1: "deepseek", 2: "deepseek", 3: "deepseek" };
  if (c.mode === "all_openrouter") return { 1: "openrouter", 2: "openrouter", 3: "openrouter" };
  return { 1: c.stage1_provider, 2: "deepseek", 3: c.stage3_provider };
}

// Mirrors backend/subtrans/llm/factory.py: with `keys`, a stage whose direct key is missing is
// routed through OpenRouter (same model) when an OpenRouter key is available.
export function stageProviders(c: RunConfig, keys?: Keys): Record<Stage, ProviderKey> {
  const direct = directProviders(c);
  if (!keys?.openrouter) return direct;
  const route = (p: ProviderKey): ProviderKey => (keys[p] ? p : "openrouter");
  return { 1: route(direct[1]), 2: route(direct[2]), 3: route(direct[3]) };
}

/** True when the stage's chosen provider has no key of its own and goes through OpenRouter. */
export function viaOpenRouter(c: RunConfig, stage: Stage, keys?: Keys): boolean {
  return directProviders(c)[stage] !== "openrouter" && stageProviders(c, keys)[stage] === "openrouter";
}

export function stageModel(c: RunConfig, stage: Stage, keys?: Keys): string {
  const direct = directProviders(c)[stage];
  const provider = stageProviders(c, keys)[stage];
  if (direct === "openrouter") {
    return { 1: c.openrouter_stage1_model, 2: c.openrouter_stage2_model, 3: c.openrouter_stage3_model }[stage];
  }
  if (provider === "openrouter") {
    const byProvider: Record<ProviderKey, string> = {
      gemini: c.openrouter_stage1_model,
      deepseek: c.openrouter_stage2_model,
      claude: c.openrouter_stage3_model,
      openrouter: c.openrouter_stage2_model,
    };
    return byProvider[direct];
  }
  return { deepseek: c.deepseek_model, gemini: c.gemini_model, claude: c.claude_model }[provider];
}

export function missingProviders(c: RunConfig, keys: Record<ProviderKey, boolean> | undefined, stages: Stage[] = [1, 2, 3]): ProviderKey[] {
  if (!keys) return [];
  const providers = stageProviders(c, keys);
  return [...new Set(stages.map((s) => providers[s]))].filter((p) => !keys[p]);
}

export const PROVIDER_LABEL: Record<ProviderKey, string> = {
  deepseek: "DeepSeek",
  claude: "Anthropic",
  gemini: "Google Gemini",
  openrouter: "OpenRouter",
};

const MODEL_LABELS: [RegExp, string][] = [
  [/deepseek.*(v4\.1-)?flash/i, "DeepSeek V4.1 Flash"],
  [/deepseek.*pro/i, "DeepSeek V4 Pro"],
  [/gemini-4-argon/i, "Gemini 4 Argon"],
  [/claude-sonnet-5[-.]5/i, "Claude Sonnet 5.5"],
  [/claude-opus-5[-.]5/i, "Claude Opus 5.5"],
  [/claude-haiku-5[-.]5/i, "Claude Haiku 5.5"],
];

export function modelLabel(model: string): string {
  for (const [re, label] of MODEL_LABELS) if (re.test(model)) return label;
  return model;
}

export const FALLBACK_DEFAULTS: RunConfig = {
  mode: "all_deepseek",
  stage1_provider: "gemini",
  stage3_provider: "claude",
  deepseek_model: "deepseek-flash",
  gemini_model: "gemini-4-argon",
  claude_model: "claude-sonnet-5-5",
  openrouter_stage1_model: "google/gemini-4-argon",
  openrouter_stage2_model: "deepseek/deepseek-v4.1-flash",
  openrouter_stage3_model: "anthropic/claude-sonnet-5.5",
  deepseek_send_thinking_flag: true,
  deepseek_send_chat_template_flag: true,
  stage2_temperature: 0.3,
  stage1_reasoning_effort: "low",
  qa_effort: "medium",
  claude_refusal_fallback: true,
  lanes: 4,
  target_size: 30,
  max_size: 42,
  retry_chunk_size: 15,
  gap_sec: 1.5,
  context_window: 6,
  bible_max_lines: 1200,
  cps_threshold: 20,
  qa_window: 120,
  qa_concurrency: 3,
  pause_after_bible: false,
};
