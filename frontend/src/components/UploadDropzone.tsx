import { BookOpenText, FileUp, Languages, ShieldCheck, Sparkles, UploadCloud } from "lucide-react";
import { useEffect, useRef, useState } from "react";
import { Button, Spinner, cx } from "./ui";

export function isSrt(file: File): boolean {
  return file.name.toLowerCase().endsWith(".srt");
}

export function pickSrtFile(): Promise<File | null> {
  return new Promise((resolve) => {
    const input = document.createElement("input");
    input.type = "file";
    input.accept = ".srt,application/x-subrip";
    input.onchange = () => resolve(input.files?.[0] ?? null);
    input.click();
  });
}

/** Window-level drag & drop: returns whether a file is being dragged over the page. */
export function useSrtDrop(onFile: (file: File) => void, enabled = true): boolean {
  const [dragging, setDragging] = useState(false);
  const depth = useRef(0);
  const cb = useRef(onFile);
  cb.current = onFile;

  useEffect(() => {
    if (!enabled) return;
    const hasFiles = (e: DragEvent) => Array.from(e.dataTransfer?.types ?? []).includes("Files");
    const enter = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current += 1;
      setDragging(true);
    };
    const over = (e: DragEvent) => {
      if (hasFiles(e)) e.preventDefault();
    };
    const leave = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      depth.current = Math.max(0, depth.current - 1);
      if (depth.current === 0) setDragging(false);
    };
    const drop = (e: DragEvent) => {
      if (!hasFiles(e)) return;
      e.preventDefault();
      depth.current = 0;
      setDragging(false);
      const file = e.dataTransfer?.files?.[0];
      if (file) cb.current(file);
    };
    window.addEventListener("dragenter", enter);
    window.addEventListener("dragover", over);
    window.addEventListener("dragleave", leave);
    window.addEventListener("drop", drop);
    return () => {
      window.removeEventListener("dragenter", enter);
      window.removeEventListener("dragover", over);
      window.removeEventListener("dragleave", leave);
      window.removeEventListener("drop", drop);
    };
  }, [enabled]);

  return dragging;
}

export function DropOverlay({ visible }: { visible: boolean }) {
  if (!visible) return null;
  return (
    <div className="pointer-events-none fixed inset-0 z-[70] flex items-center justify-center bg-bg/80 backdrop-blur-sm">
      <div className="rounded-2xl border-2 border-dashed border-accent bg-accent-soft px-10 py-8 text-center">
        <UploadCloud className="mx-auto text-accent" size={36} />
        <div className="mt-3 text-lg font-semibold text-ink">Drop your .srt file</div>
        <div className="text-sm text-muted">Timestamps stay locked — only text is translated.</div>
      </div>
    </div>
  );
}

const STEPS = [
  {
    icon: <BookOpenText size={18} />,
    title: "1 · Translation Bible",
    body: "Gemini 4 Argon or DeepSeek reads up to 1,200 lines and extracts tone, pronoun-formality rules and a glossary you can edit.",
  },
  {
    icon: <Languages size={18} />,
    title: "2 · Sliding-window translation",
    body: "DeepSeek V4.1 Flash translates scene-aware batches with 6 lines of context on each side and a strict 1-to-1 ID lock.",
  },
  {
    icon: <Sparkles size={18} />,
    title: "3 · Flagship QA critic",
    body: "Claude Sonnet 5.5 or Gemini 4 Argon fixes split-sentence grammar, idioms, glossary drift, leaks and high-CPS lines as a sparse patch.",
  },
];

export function EmptyDropzone({ onFile, uploading }: { onFile: (f: File) => void; uploading: boolean }) {
  const [hover, setHover] = useState(false);
  return (
    <div className="flex h-full items-start justify-center overflow-y-auto p-4 sm:items-center sm:p-8">
      <div className="w-full max-w-3xl">
        <div
          onDragOver={(e) => {
            e.preventDefault();
            setHover(true);
          }}
          onDragLeave={() => setHover(false)}
          onDrop={() => setHover(false)}
          className={cx(
            "rounded-2xl border-2 border-dashed p-8 text-center transition sm:p-12",
            hover ? "border-accent bg-accent-soft" : "border-line-strong bg-panel",
          )}
        >
          <div className="mx-auto flex h-14 w-14 items-center justify-center rounded-2xl bg-accent-soft text-accent">
            {uploading ? <Spinner className="h-6 w-6" /> : <FileUp size={26} />}
          </div>
          <h1 className="mt-4 text-xl font-semibold tracking-tight text-ink">Localize a subtitle file</h1>
          <p className="mx-auto mt-1.5 max-w-md text-sm text-muted">
            Drag &amp; drop an <span className="font-mono text-ink-2">.srt</span> anywhere on this page — multi-thousand-line
            files are fine. Pick the target language above, review the estimate, then start.
          </p>
          <div className="mt-5 flex justify-center">
            <Button
              variant="primary"
              icon={<UploadCloud size={16} />}
              disabled={uploading}
              onClick={async () => {
                const file = await pickSrtFile();
                if (file) onFile(file);
              }}
            >
              {uploading ? "Parsing…" : "Choose .srt file"}
            </Button>
          </div>
          <div className="mt-4 inline-flex items-center gap-1.5 text-xs text-muted">
            <ShieldCheck size={13} className="text-ok" /> Timestamps never leave the server and are exported byte-for-byte.
          </div>
        </div>
        <div className="mt-4 grid gap-3 sm:grid-cols-3">
          {STEPS.map((s) => (
            <div key={s.title} className="rounded-xl border border-line bg-panel p-4">
              <div className="flex items-center gap-2 text-sm font-semibold text-ink">
                <span className="text-accent">{s.icon}</span>
                {s.title}
              </div>
              <p className="mt-1.5 text-xs leading-relaxed text-muted">{s.body}</p>
            </div>
          ))}
        </div>
      </div>
    </div>
  );
}
