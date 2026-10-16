import { Globe2, X } from "lucide-react";
import { useState } from "react";
import { LANGUAGES } from "../lib/languages";
import { Select, TextInput } from "./ui";

const CUSTOM = "__custom__";

export function LanguageSelect({
  value,
  onChange,
  disabled,
}: {
  value: string;
  onChange: (v: string) => void;
  disabled?: boolean;
}) {
  const [custom, setCustom] = useState(() => value !== "" && !LANGUAGES.includes(value));

  if (custom) {
    return (
      <div className="relative w-full">
        <Globe2 size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
        <TextInput
          aria-label="Custom target language"
          autoFocus={!disabled && value === ""}
          className="pl-8 pr-8"
          placeholder="e.g. Swiss German, formal"
          value={value}
          disabled={disabled}
          onChange={(e) => onChange(e.target.value)}
        />
        {!disabled && (
          <button
            type="button"
            className="absolute right-2 top-1/2 -translate-y-1/2 text-muted hover:text-ink"
            aria-label="Back to language list"
            onClick={() => {
              setCustom(false);
              onChange("");
            }}
          >
            <X size={14} />
          </button>
        )}
      </div>
    );
  }

  return (
    <div className="relative w-full">
      <Globe2 size={15} className="pointer-events-none absolute left-2.5 top-1/2 -translate-y-1/2 text-muted" />
      <Select
        aria-label="Target language"
        className="pl-8"
        value={value}
        disabled={disabled}
        onChange={(e) => {
          if (e.target.value === CUSTOM) {
            setCustom(true);
            onChange("");
          } else onChange(e.target.value);
        }}
      >
        <option value="">Target language…</option>
        {LANGUAGES.map((l) => (
          <option key={l} value={l}>
            {l}
          </option>
        ))}
        <option value={CUSTOM}>Custom language…</option>
      </Select>
    </div>
  );
}
