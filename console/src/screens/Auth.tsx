import { useState } from 'react';
import type { FormEvent, ReactNode } from 'react';
import { browserSupportsWebAuthn } from '@simplewebauthn/browser';
import { api, describeError, passkeyAdd, passkeyLogin, passkeySetup, setSession } from '../api';
import type { SessionInfo } from '../api';
import { navigate } from '../router';

function Shell({ title, children }: { title: string; children: ReactNode }) {
  return (
    <main className="auth" id="main">
      <div className="card auth-card">
        <div className="brand">
          <span className="brand-seal" aria-hidden="true">
            番
          </span>
          Bansho
        </div>
        <h1 style={{ marginBottom: 12 }}>{title}</h1>
        {children}
      </div>
    </main>
  );
}

function NoWebAuthn() {
  return <p className="note err">This browser does not support passkeys (WebAuthn). Use a current Chrome, Safari, Edge or Firefox.</p>;
}

export function CodesBlock({ codes }: { codes: string[] }) {
  const [copied, setCopied] = useState(false);
  return (
    <div>
      <div className="codes" aria-label="Recovery codes">
        {codes.map((c) => (
          <span key={c}>{c}</span>
        ))}
      </div>
      <button
        style={{ marginTop: 8 }}
        onClick={() => {
          void navigator.clipboard?.writeText(codes.join('\n')).then(() => setCopied(true));
        }}
      >
        {copied ? 'Copied' : 'Copy all'}
      </button>
    </div>
  );
}

export function Login({ onSignedIn }: { onSignedIn: (s: SessionInfo) => void }) {
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      onSignedIn(await passkeyLogin());
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Sign in">
      {!browserSupportsWebAuthn() ? <NoWebAuthn /> : null}
      <p className="muted">Private console. Use your passkey.</p>
      <button className="primary" onClick={go} disabled={busy} style={{ width: '100%' }}>
        {busy ? 'Waiting for your passkey…' : 'Sign in with passkey'}
      </button>
      {err ? (
        <p className="note err" role="alert" style={{ marginTop: 12 }}>
          {err}
        </p>
      ) : null}
      <p className="small" style={{ marginTop: 16 }}>
        <button className="link" onClick={() => navigate('/recover')}>
          Lost your passkey? Use a recovery code
        </button>
      </p>
      {import.meta.env.DEV ? <DevLogin onSignedIn={onSignedIn} /> : null}
    </Shell>
  );
}

// Development only. `import.meta.env.DEV` is a compile-time false in
// `vite build`, so this component and its endpoint string are removed from
// the production bundle; the backend also refuses the route outside dev mode.
function DevLogin({ onSignedIn }: { onSignedIn: (s: SessionInfo) => void }) {
  const [err, setErr] = useState<string | null>(null);
  if (!import.meta.env.DEV) return null;
  return (
    <div className="note" style={{ marginTop: 16 }}>
      <strong>Dev / seed mode.</strong> Signs in without a passkey; the server allows this only on localhost with BANSHO_DEV_SEED=1.
      <div style={{ marginTop: 8 }}>
        <button
          onClick={async () => {
            try {
              const r = await api.post<{ session: SessionInfo }>(import.meta.env.DEV ? '/api/dev/login' : '');
              setSession(r.session);
              onSignedIn(r.session);
            } catch (e) {
              setErr(describeError(e));
            }
          }}
        >
          Dev sign-in
        </button>
        {err ? <span className="small"> {err}</span> : null}
      </div>
    </div>
  );
}

export function Setup({ onSignedIn }: { onSignedIn: (s: SessionInfo) => void }) {
  const [token, setToken] = useState('');
  const [name, setName] = useState('My phone');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const [done, setDone] = useState<{ session: SessionInfo; codes: string[] } | null>(null);
  const [saved, setSaved] = useState(false);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await passkeySetup(token.trim(), name.trim() || 'Passkey');
      setToken('');
      setDone({ session: r.session, codes: r.recovery_codes });
    } catch (e2) {
      setErr(e2 instanceof Error && 'status' in e2 && (e2 as { status: number }).status === 404 ? 'Setup is not available (wrong token, or a passkey already exists).' : describeError(e2));
    } finally {
      setBusy(false);
    }
  };
  if (done) {
    return (
      <Shell title="Save your recovery codes">
        <p>
          Your passkey is registered. These <strong>10 recovery codes</strong> are shown <strong>once</strong>. Each can be used one time to register a new
          passkey if you lose this one. Store them offline (password manager or paper).
        </p>
        <CodesBlock codes={done.codes} />
        <label className="row" style={{ flexDirection: 'row', marginTop: 12, fontSize: 14, color: 'var(--ink)' }}>
          <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} style={{ minHeight: 0 }} /> I have saved these codes
        </label>
        <button
          className="primary"
          disabled={!saved}
          style={{ width: '100%', marginTop: 12 }}
          onClick={() => {
            navigate('/');
            onSignedIn(done.session);
          }}
        >
          Continue to the console
        </button>
      </Shell>
    );
  }
  return (
    <Shell title="First-time setup">
      {!browserSupportsWebAuthn() ? <NoWebAuthn /> : null}
      <p className="muted small">Works once: after the first passkey exists this page answers “not available” forever.</p>
      <form className="stack" onSubmit={submit}>
        <label>
          Setup token (BANSHO_SETUP_TOKEN)
          <input type="password" autoComplete="off" value={token} onChange={(e) => setToken(e.target.value)} required minLength={32} />
        </label>
        <label>
          Name this passkey
          <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
        </label>
        <button className="primary" type="submit" disabled={busy || token.trim().length < 32}>
          {busy ? 'Follow your device’s prompt…' : 'Create passkey'}
        </button>
      </form>
      {err ? (
        <p className="note err" role="alert" style={{ marginTop: 12 }}>
          {err}
        </p>
      ) : null}
    </Shell>
  );
}

export function Recover({ onSignedIn }: { onSignedIn: (s: SessionInfo) => void }) {
  const [code, setCode] = useState('');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const submit = async (e: FormEvent) => {
    e.preventDefault();
    setBusy(true);
    setErr(null);
    try {
      const r = await api.post<{ session: SessionInfo }>('/api/auth/recovery', { code });
      setSession(r.session);
      setCode('');
      onSignedIn(r.session);
    } catch (e2) {
      setErr(describeError(e2));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Recovery">
      <p className="muted small">A recovery code lets you register one new passkey — nothing else. Each code works once.</p>
      <form className="stack" onSubmit={submit}>
        <label>
          Recovery code
          <input value={code} onChange={(e) => setCode(e.target.value)} autoComplete="off" spellCheck={false} placeholder="xxxxx-xxxxx-xxxxx-xxxxx" required />
        </label>
        <button className="primary" type="submit" disabled={busy || code.replace(/[^a-z0-9]/gi, '').length !== 20}>
          Continue
        </button>
      </form>
      {err ? (
        <p className="note err" role="alert" style={{ marginTop: 12 }}>
          {err}
        </p>
      ) : null}
      <p className="small" style={{ marginTop: 16 }}>
        <button className="link" onClick={() => navigate('/login')}>
          Back to sign in
        </button>
      </p>
    </Shell>
  );
}

export function RecoveryRegister({ onDone }: { onDone: () => void }) {
  const [name, setName] = useState('Replacement passkey');
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState<string | null>(null);
  const go = async () => {
    setBusy(true);
    setErr(null);
    try {
      await passkeyAdd(name.trim() || 'Passkey');
      setSession(null);
      onDone();
    } catch (e) {
      setErr(describeError(e));
    } finally {
      setBusy(false);
    }
  };
  return (
    <Shell title="Register a new passkey">
      <p>
        You are signed in with a recovery code. The only thing this session can do is register a new passkey (it expires in 15 minutes). Then sign in
        with it.
      </p>
      <label>
        Name
        <input value={name} onChange={(e) => setName(e.target.value)} maxLength={40} />
      </label>
      <button className="primary" onClick={go} disabled={busy} style={{ width: '100%', marginTop: 12 }}>
        {busy ? 'Follow your device’s prompt…' : 'Create passkey'}
      </button>
      {err ? (
        <p className="note err" role="alert" style={{ marginTop: 12 }}>
          {err}
        </p>
      ) : null}
    </Shell>
  );
}
