import { Clapperboard, Loader2, LockKeyhole, LogIn } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, ApiError } from "../api";
import { Button, TextInput } from "./ui";

export function LoginPage({ onSignedIn, notConfigured }: { onSignedIn: () => void; notConfigured: boolean }) {
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const submit = async (e: FormEvent) => {
    e.preventDefault();
    if (!password || busy) return;
    setBusy(true);
    setError(null);
    try {
      await api.login(password);
      setPassword("");
      onSignedIn();
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Could not sign in.");
    } finally {
      setBusy(false);
    }
  };

  return (
    <div className="flex h-full items-center justify-center bg-bg p-4">
      <form onSubmit={submit} className="w-full max-w-sm rounded-2xl border border-line bg-panel p-6 shadow-sm sm:p-8">
        <div className="flex items-center gap-2.5">
          <span className="flex h-9 w-9 items-center justify-center rounded-lg bg-accent text-accent-ink">
            <Clapperboard size={18} />
          </span>
          <div className="leading-tight">
            <div className="text-sm font-semibold tracking-tight text-ink">SubTrans Studio</div>
            <div className="text-[11px] text-muted">.srt localization &amp; QA</div>
          </div>
        </div>

        <h1 className="mt-6 text-lg font-semibold tracking-tight text-ink">Sign in</h1>
        <p className="mt-1 text-sm text-muted">This workspace is private. Enter the access password.</p>

        {notConfigured ? (
          <p className="mt-5 rounded-lg border border-warn/40 bg-warn-soft px-3 py-2 text-sm text-ink-2">
            Login isn't configured on the server yet. Set <span className="font-mono">APP_PASSWORD</span> in the
            environment and redeploy.
          </p>
        ) : (
          <>
            <label className="mt-5 block">
              <span className="mb-1 block text-xs font-medium text-ink-2">Password</span>
              <div className="relative">
                <LockKeyhole size={15} className="pointer-events-none absolute left-3 top-1/2 -translate-y-1/2 text-muted" />
                <TextInput
                  type="password"
                  name="password"
                  autoComplete="current-password"
                  autoFocus
                  required
                  value={password}
                  onChange={(e) => setPassword(e.target.value)}
                  className="pl-9"
                  aria-invalid={!!error}
                  aria-describedby={error ? "login-error" : undefined}
                />
              </div>
            </label>
            {error && (
              <p id="login-error" role="alert" className="mt-2 text-sm text-danger">
                {error}
              </p>
            )}
            <Button
              type="submit"
              variant="primary"
              className="mt-5 w-full"
              disabled={busy || !password}
              icon={busy ? <Loader2 size={15} className="animate-spin" /> : <LogIn size={15} />}
            >
              {busy ? "Signing in…" : "Sign in"}
            </Button>
          </>
        )}
      </form>
    </div>
  );
}
