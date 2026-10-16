import { useCallback, useMemo, useState } from "react";
import { FALLBACK_DEFAULTS } from "../lib/pipeline";
import type { RunConfig } from "../types";

// Non-secret preferences only. API keys live in backend/.env and never touch the browser.
const KEY = "subtrans.settings.v1";

interface Stored {
  config?: Partial<RunConfig>;
  targetLanguage?: string;
  contextHints?: string;
}

function load(): Stored {
  try {
    const raw = localStorage.getItem(KEY);
    return raw ? (JSON.parse(raw) as Stored) : {};
  } catch {
    return {};
  }
}

function save(s: Stored) {
  try {
    localStorage.setItem(KEY, JSON.stringify(s));
  } catch {
    /* storage unavailable (private mode) — settings stay in memory */
  }
}

export function useSettings(serverDefaults: RunConfig | null) {
  const [stored, setStored] = useState<Stored>(load);
  const defaults = serverDefaults ?? FALLBACK_DEFAULTS;

  const config = useMemo<RunConfig>(() => ({ ...defaults, ...stored.config }), [defaults, stored.config]);

  const update = useCallback((patch: Stored) => {
    setStored((prev) => {
      const next = { ...prev, ...patch };
      save(next);
      return next;
    });
  }, []);

  const setConfig = useCallback(
    (patch: Partial<RunConfig>) => {
      setStored((prev) => {
        const next = { ...prev, config: { ...prev.config, ...patch } };
        save(next);
        return next;
      });
    },
    [],
  );

  const resetConfig = useCallback(() => update({ config: {} }), [update]);

  return {
    config,
    defaults,
    targetLanguage: stored.targetLanguage ?? "",
    contextHints: stored.contextHints ?? "",
    setConfig,
    resetConfig,
    setTargetLanguage: (v: string) => update({ targetLanguage: v }),
    setContextHints: (v: string) => update({ contextHints: v }),
  };
}

export type SettingsApi = ReturnType<typeof useSettings>;
