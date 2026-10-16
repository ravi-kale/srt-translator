// Mirrors backend/subtrans/models.py

export type Flag = "HIGH_CPS" | "EMPTY" | "IDENTICAL" | "MISSING";

export interface Block {
  id: string;
  index: number;
  timestamp: string;
  start_sec: number;
  end_sec: number;
  duration_sec: number;
  source_text: string;
  draft_translation: string;
  final_translation: string;
  cps: number;
  qa_fixed: boolean;
  qa_reason: string;
  flags: Flag[];
  user_edited: boolean;
}

export interface GlossaryEntry {
  source_term: string;
  target_term: string;
  notes: string;
}

export interface PronounRule {
  speakers: string;
  rule: string;
}

export interface Bible {
  synopsis_and_tone: string;
  speaker_pronoun_rules: PronounRule[];
  glossary: GlossaryEntry[];
}

export type PipelineMode = "hybrid" | "all_deepseek" | "all_openrouter";
export type Effort = "low" | "medium" | "high";

export interface RunConfig {
  mode: PipelineMode;
  stage1_provider: "gemini" | "deepseek";
  stage3_provider: "claude" | "gemini";
  deepseek_model: string;
  gemini_model: string;
  claude_model: string;
  openrouter_stage1_model: string;
  openrouter_stage2_model: string;
  openrouter_stage3_model: string;
  deepseek_send_thinking_flag: boolean;
  deepseek_send_chat_template_flag: boolean;
  stage2_temperature: number;
  stage1_reasoning_effort: "minimal" | "low" | "medium" | "high";
  qa_effort: Effort;
  claude_refusal_fallback: boolean;
  lanes: number;
  target_size: number;
  max_size: number;
  retry_chunk_size: number;
  gap_sec: number;
  context_window: number;
  bible_max_lines: number;
  cps_threshold: number;
  qa_window: number;
  qa_concurrency: number;
  pause_after_bible: boolean;
}

export type JobStatus =
  | "ready"
  | "bible_running"
  | "awaiting_review"
  | "translating"
  | "qa_running"
  | "completed"
  | "paused"
  | "interrupted"
  | "failed";

export const RUNNING: ReadonlySet<JobStatus> = new Set(["bible_running", "translating", "qa_running"]);

export interface Progress {
  stage: number;
  batches_total: number;
  batches_done: number;
  qa_windows_total: number;
  qa_windows_done: number[];
  qa_window_size: number;
  qa_fixes: number;
  last_message: string;
  started_at: number | null;
  finished_at: number | null;
}

export interface StageUsage {
  calls: number;
  input_tokens: number;
  cache_hit_tokens: number;
  cache_write_tokens: number;
  output_tokens: number;
  cost_usd: number;
}

export interface UsageTotals {
  stage1: StageUsage;
  stage2: StageUsage;
  stage3: StageUsage;
}

export interface JobSummary {
  id: string;
  filename: string;
  created_at: number;
  updated_at: number;
  status: JobStatus;
  target_language: string;
  context_hints: string;
  block_count: number;
  progress: Progress;
  total_cost_usd: number;
  error: string | null;
  has_bible: boolean;
  bible_approved: boolean;
}

export interface JobDetail {
  job: JobSummary;
  run_config: RunConfig | null;
  bible: Bible | null;
  usage: UsageTotals;
  total_cost_usd: number;
  blocks: Block[];
  rev: number;
  logs: LogEntry[];
}

/** Incremental poll: full small state + only the blocks/logs changed since the cursors. */
export interface JobChanges {
  job: JobSummary;
  bible: Bible | null;
  usage: UsageTotals;
  total_cost_usd: number;
  blocks: Block[];
  rev: number;
  logs: LogEntry[];
}

export interface AuthSession {
  auth_required: boolean;
  configured: boolean;
  authenticated: boolean;
}

export type ProviderKey = "deepseek" | "claude" | "gemini" | "openrouter";

export interface AppConfig {
  keys: Record<ProviderKey, boolean>;
  key_env_names: Record<ProviderKey, string>;
  defaults: RunConfig;
  base_urls: Record<string, string>;
}

export interface StageEstimate {
  stage: 1 | 2 | 3;
  provider: string;
  model: string;
  input_tokens: number;
  cached_tokens: number;
  output_tokens: number;
  cost_usd: number;
  priced: boolean;
  calls: number;
  skipped?: boolean;
}

export interface Estimate {
  stages: StageEstimate[];
  total_cost_usd: number;
  total_input_tokens: number;
  total_output_tokens: number;
  batches: number;
  qa_windows: number;
  lines: number;
  spent_usd: number;
}

export interface LogEntry {
  id: number;
  message: string;
  level: "info" | "warning" | "error";
  ts: number;
}

export interface RunRequest {
  config: RunConfig;
  target_language?: string;
  context_hints?: string;
}
