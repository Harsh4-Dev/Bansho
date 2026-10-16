import { createContext, useCallback, useContext, useEffect, useRef, useState } from 'react';
import type { ReactNode } from 'react';
import { api, describeError } from '../api';

// ---- data hook ---------------------------------------------------------------

export interface Loaded<T> {
  data: T | null;
  error: string | null;
  loading: boolean;
  reload: () => void;
}

export function useApi<T>(path: string | null, refreshMs = 0): Loaded<T> {
  const [data, setData] = useState<T | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [loading, setLoading] = useState(false);
  const [tick, setTick] = useState(0);
  const auto = useRef(false);
  useEffect(() => {
    if (!path) return;
    let live = true;
    setLoading(true);
    const background = auto.current;
    auto.current = false;
    api
      .get<T>(path, background)
      .then((d) => {
        if (!live) return;
        setData(d);
        setError(null);
      })
      .catch((e: unknown) => live && setError(describeError(e)))
      .finally(() => live && setLoading(false));
    return () => {
      live = false;
    };
  }, [path, tick]);
  useEffect(() => {
    if (!refreshMs) return;
    const t = window.setInterval(() => {
      if (document.visibilityState === 'visible') {
        auto.current = true;
        setTick((x) => x + 1);
      }
    }, refreshMs);
    return () => window.clearInterval(t);
  }, [refreshMs]);
  const reload = useCallback(() => setTick((x) => x + 1), []);
  return { data, error, loading, reload };
}

// ---- small pieces -------------------------------------------------------------

export function Card({ title, actions, children, className = '' }: { title?: string; actions?: ReactNode; children: ReactNode; className?: string }) {
  return (
    <section className={`card ${className}`}>
      {title || actions ? (
        <div className="card-head">
          {title ? <h2>{title}</h2> : <span />}
          {actions}
        </div>
      ) : null}
      {children}
    </section>
  );
}

export function Kpi({ label, value, sub, bad = false }: { label: string; value: ReactNode; sub?: ReactNode; bad?: boolean }) {
  return (
    <div className={`kpi${bad ? ' bad' : ''}`}>
      <div className="kpi-label">{label}</div>
      <div className="kpi-value num">{value}</div>
      {sub !== undefined ? <div className="kpi-sub">{sub}</div> : null}
    </div>
  );
}

const STATE_LABEL: Record<string, string> = {
  up: 'Up',
  asleep: 'Asleep',
  down: 'Down',
  misconfigured: 'Token rejected',
  not_configured: 'Not configured',
  unknown: 'No checks yet',
};

export function StateBadge({ state }: { state: string }) {
  return <span className={`badge st-${state}`}>{STATE_LABEL[state] ?? state}</span>;
}

export function LevelBadge({ level }: { level: string }) {
  const label = level === 'crit' ? 'Critical' : level === 'warn' ? 'Warning' : 'Info';
  return <span className={`badge plain lv-${level}`}>{label}</span>;
}

export function Loading({ what = 'Loading' }: { what?: string }) {
  return (
    <p className="muted" role="status">
      {what}…
    </p>
  );
}

export function ErrorNote({ error, onRetry }: { error: string; onRetry?: () => void }) {
  return (
    <div className="note err" role="alert">
      {error}{' '}
      {onRetry ? (
        <button className="link" onClick={onRetry}>
          Retry
        </button>
      ) : null}
    </div>
  );
}

export interface UpstreamInfo {
  ok: boolean;
  status: number;
  error: string | null;
  latency_ms: number;
  fetched_at: number;
  cached: boolean;
}

const UPSTREAM_TEXT: Record<string, string> = {
  not_configured: 'is not configured on the console (URL or ops token missing / shorter than 48 characters).',
  unauthorized: 'rejected the ops token (it answers 404). The token on both sides must be identical.',
  timeout: 'did not answer in time.',
  unreachable: 'could not be reached.',
  http_error: 'returned an error.',
  bad_json: 'returned something that is not JSON.',
};

/** Explains a failed upstream call in plain words. */
export function UpstreamNote({ app, up, message, asleepOk = false }: { app: string; up: UpstreamInfo | undefined; message?: string | null; asleepOk?: boolean }) {
  if (!up || up.ok) return null;
  const err = up.error ?? 'http_error';
  if (asleepOk && (err === 'timeout' || err === 'unreachable' || [502, 503, 504].includes(up.status))) {
    return (
      <div className="note">
        {app} is asleep (Render free tier sleeps after 15 idle minutes). Showing the last collected snapshot.
      </div>
    );
  }
  return (
    <div className="note err" role="alert">
      {app} {UPSTREAM_TEXT[err] ?? 'failed.'}
      {up.status ? ` HTTP ${up.status}.` : ''} {message ?? ''}
    </div>
  );
}

export function Tabs<T extends string>({ tabs, value, onChange, label }: { tabs: Array<[T, string]>; value: T; onChange: (t: T) => void; label: string }) {
  return (
    <div className="tabs" role="tablist" aria-label={label}>
      {tabs.map(([k, name]) => (
        <button key={k} role="tab" aria-selected={value === k} onClick={() => onChange(k)}>
          {name}
        </button>
      ))}
    </div>
  );
}

export function Table({ head, rows, empty = 'Nothing to show.', right = [] }: { head: string[]; rows: ReactNode[][]; empty?: string; right?: number[] }) {
  if (!rows.length) return <p className="muted small">{empty}</p>;
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            {head.map((h, i) => (
              <th key={h} className={right.includes(i) ? 'r' : undefined} scope="col">
                {h}
              </th>
            ))}
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={i}>
              {r.map((c, j) => (
                <td key={j} className={right.includes(j) ? 'r' : undefined}>
                  {c}
                </td>
              ))}
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// ---- confirmation dialog + toast ------------------------------------------------

interface ConfirmOpts {
  title: string;
  body: ReactNode;
  confirmLabel?: string;
  danger?: boolean;
  stepUp?: boolean;
}

type ConfirmFn = (o: ConfirmOpts) => Promise<boolean>;
const Ctx = createContext<{ confirm: ConfirmFn; toast: (msg: string) => void } | null>(null);

export function FeedbackProvider({ children }: { children: ReactNode }) {
  const [open, setOpen] = useState<(ConfirmOpts & { resolve: (v: boolean) => void }) | null>(null);
  const [toastMsg, setToast] = useState<string | null>(null);
  const confirmBtn = useRef<HTMLButtonElement>(null);
  const confirm = useCallback<ConfirmFn>((o) => new Promise<boolean>((resolve) => setOpen({ ...o, resolve })), []);
  const toast = useCallback((msg: string) => {
    setToast(msg);
    window.setTimeout(() => setToast((m) => (m === msg ? null : m)), 4000);
  }, []);
  useEffect(() => {
    if (!open) return;
    confirmBtn.current?.focus();
    const onKey = (e: KeyboardEvent) => {
      if (e.key === 'Escape') {
        open.resolve(false);
        setOpen(null);
      }
    };
    window.addEventListener('keydown', onKey);
    return () => window.removeEventListener('keydown', onKey);
  }, [open]);
  const close = (v: boolean) => {
    open?.resolve(v);
    setOpen(null);
  };
  return (
    <Ctx.Provider value={{ confirm, toast }}>
      {children}
      {open ? (
        <div className="backdrop" role="presentation">
          <div className="dialog" role="alertdialog" aria-modal="true" aria-labelledby="dlg-title" aria-describedby="dlg-body">
            <h2 id="dlg-title">{open.title}</h2>
            <div id="dlg-body">{open.body}</div>
            {open.stepUp ? (
              <p className="small muted">This is a destructive action: your passkey will be asked for again if you have not confirmed it in the last 5 minutes.</p>
            ) : null}
            <div className="dialog-actions">
              <button onClick={() => close(false)}>Cancel</button>
              <button ref={confirmBtn} className={open.danger ? 'primary' : ''} onClick={() => close(true)}>
                {open.confirmLabel ?? 'Confirm'}
              </button>
            </div>
          </div>
        </div>
      ) : null}
      {toastMsg ? (
        <div className="toast" role="status" aria-live="polite">
          {toastMsg}
        </div>
      ) : null}
    </Ctx.Provider>
  );
}

export function useFeedback() {
  const c = useContext(Ctx);
  if (!c) throw new Error('FeedbackProvider missing');
  return c;
}

/** Run an action after confirmation, report the outcome in a toast. */
export function useAction() {
  const { confirm, toast } = useFeedback();
  const [busy, setBusy] = useState(false);
  const run = useCallback(
    async <T,>(opts: ConfirmOpts, fn: () => Promise<T>, done?: (r: T) => void, okMsg = 'Done.') => {
      if (!(await confirm(opts))) return;
      setBusy(true);
      try {
        const r = await fn();
        toast(okMsg);
        done?.(r);
      } catch (e) {
        toast(describeError(e));
      } finally {
        setBusy(false);
      }
    },
    [confirm, toast],
  );
  return { run, busy };
}
