import { ReactNode, useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "./api";

export function Loading({ label = "Working…" }: { label?: string }) {
  return (
    <div className="loading" role="status">
      <span className="spin" /> {label}
    </div>
  );
}

export function ErrorNote({ error, onRetry }: { error: unknown; onRetry?: () => void }) {
  if (!error) return null;
  const e = error as ApiError;
  return (
    <div className="note bad" role="alert">
      <strong>{e.offline ? "Not saved. " : ""}</strong>
      {e.message || "Something went wrong."}
      {e.code && !e.offline ? <span className="muted small"> ({e.code})</span> : null}
      {onRetry ? (
        <button className="btn sm" style={{ marginLeft: 10 }} onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export type Kind = "observed" | "predicted" | "simulated";
export function KindChip({ kind, children }: { kind: Kind; children?: ReactNode }) {
  const cls = kind === "observed" ? "obs" : kind === "predicted" ? "pred" : "sim";
  const word = kind === "observed" ? "Observed" : kind === "predicted" ? "Predicted" : "Simulated";
  return (
    <span className={`chip ${cls}`}>
      <span className="dot" aria-hidden /> {children || word}
    </span>
  );
}

export function Field({ label, hint, children }: { label: string; hint?: string; children: ReactNode }) {
  return (
    <label className="field">
      <span>{label}</span>
      {children}
      {hint ? <small>{hint}</small> : null}
    </label>
  );
}

export function Seg<T extends string>({ value, options, onChange, wide, label }: { value: T; options: Array<[T, string]>; onChange: (v: T) => void; wide?: boolean; label: string }) {
  return (
    <div className={`seg${wide ? " wide" : ""}`} role="group" aria-label={label}>
      {options.map(([v, text]) => (
        <button key={v} type="button" aria-pressed={v === value} onClick={() => onChange(v)}>
          {text}
        </button>
      ))}
    </div>
  );
}

export function Kpi({ k, v, s, xl }: { k: string; v: ReactNode; s?: ReactNode; xl?: boolean }) {
  return (
    <div className="kpi">
      <span className="k">{k}</span>
      <span className={`v num${xl ? " xl" : ""}`}>{v}</span>
      {s ? <span className="s">{s}</span> : null}
    </div>
  );
}

/** Load data with explicit loading / error / stale states. */
export function useAsync<T>(fn: () => Promise<T>, deps: unknown[]) {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<unknown>(null);
  const [loading, setLoading] = useState(true);
  const seq = useRef(0);
  const run = useCallback(() => {
    const id = ++seq.current;
    setLoading(true);
    fn()
      .then((d) => {
        if (id === seq.current) {
          setData(d);
          setError(null);
        }
      })
      .catch((e) => {
        if (id === seq.current) setError(e);
      })
      .finally(() => {
        if (id === seq.current) setLoading(false);
      });
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, deps);
  useEffect(run, [run]);
  return { data, error, loading, reload: run, stale: loading && data !== null };
}

export function useToast() {
  const [toast, setToast] = useState<{ text: string; bad?: boolean } | null>(null);
  const timer = useRef<number>();
  const show = useCallback((text: string, bad = false) => {
    window.clearTimeout(timer.current);
    setToast({ text, bad });
    timer.current = window.setTimeout(() => setToast(null), bad ? 6000 : 3200);
  }, []);
  const node = toast ? (
    <div className={`toast${toast.bad ? " bad" : ""}`} role="status">
      {toast.text}
    </div>
  ) : null;
  return { show, node };
}

export function Stepper({ value, onChange, min, max, step, format }: { value: number; onChange: (v: number) => void; min: number; max: number; step: number; format: (v: number) => string }) {
  return (
    <div className="stepper">
      <button type="button" aria-label="Less" disabled={value <= min + 1e-9} onClick={() => onChange(Math.max(min, +(value - step).toFixed(2)))}>
        −
      </button>
      <output aria-live="polite">{format(value)}</output>
      <button type="button" aria-label="More" disabled={value >= max - 1e-9} onClick={() => onChange(Math.min(max, +(value + step).toFixed(2)))}>
        +
      </button>
    </div>
  );
}
