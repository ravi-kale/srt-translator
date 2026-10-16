import type { JobStatus } from "../types";
import { Badge } from "./ui";

const LABELS: Record<JobStatus, [string, "neutral" | "accent" | "ok" | "warn" | "danger" | "qa"]> = {
  ready: ["Ready", "neutral"],
  bible_running: ["Stage 1", "accent"],
  awaiting_review: ["Review Bible", "warn"],
  translating: ["Stage 2", "accent"],
  qa_running: ["Stage 3", "qa"],
  completed: ["Completed", "ok"],
  paused: ["Paused", "warn"],
  interrupted: ["Interrupted", "warn"],
  failed: ["Failed", "danger"],
};

export function StatusBadge({ status }: { status: JobStatus }) {
  const [label, tone] = LABELS[status];
  return <Badge tone={tone}>{label}</Badge>;
}
