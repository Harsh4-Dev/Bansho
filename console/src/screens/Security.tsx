import { useState } from 'react';
import { api, describeError, passkeyAdd } from '../api';
import { Card, ErrorNote, Loading, Table, useAction, useApi, useFeedback } from '../components/ui';
import { ago, when } from '../fmt';
import { CodesBlock } from './Auth';

interface Passkey {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
  backed_up: boolean;
  transports: string[];
}

interface SessionRow {
  id: string;
  kind: string;
  current: boolean;
  created_at: string;
  last_seen_at: string;
  expires_at: string;
  client: string;
  country: string;
}

export function Security() {
  const pk = useApi<{ max: number; passkeys: Passkey[] }>('/api/security/passkeys');
  const ss = useApi<{ sessions: SessionRow[] }>('/api/security/sessions', 60_000);
  const rc = useApi<{ remaining: number; total: number }>('/api/security/recovery');
  const { run, busy } = useAction();
  const { confirm, toast } = useFeedback();
  const [codes, setCodes] = useState<string[] | null>(null);
  const [adding, setAdding] = useState(false);

  const add = async () => {
    const name = window.prompt('Name for the new passkey (e.g. "Laptop", "YubiKey")', 'Passkey');
    if (name === null) return;
    setAdding(true);
    try {
      await passkeyAdd(name.trim() || 'Passkey');
      toast('Passkey added.');
      pk.reload();
    } catch (e) {
      toast(describeError(e));
    } finally {
      setAdding(false);
    }
  };

  const rename = async (p: Passkey) => {
    const name = window.prompt('New name', p.name);
    if (!name || !name.trim()) return;
    try {
      await api.post(`/api/security/passkeys/${encodeURIComponent(p.id)}/rename`, { name: name.trim() });
      pk.reload();
    } catch (e) {
      toast(describeError(e));
    }
  };

  const regenerate = async () => {
    if (
      !(await confirm({
        title: 'Regenerate recovery codes',
        body: 'All current recovery codes stop working and 10 new ones are shown once. Save them somewhere offline.',
        danger: true,
        stepUp: true,
        confirmLabel: 'Regenerate',
      }))
    )
      return;
    try {
      const r = await api.post<{ recovery_codes: string[] }>('/api/security/recovery/regenerate');
      setCodes(r.recovery_codes);
      rc.reload();
    } catch (e) {
      toast(describeError(e));
    }
  };

  const count = pk.data?.passkeys.length ?? 0;
  return (
    <>
      <div className="page-head">
        <h1>Security</h1>
        <span className="small muted">Passkeys only — there is no password anywhere</span>
      </div>
      <div className="grid">
        <Card
          title={`Passkeys · ${count} of ${pk.data?.max ?? 5}`}
          className="span-12"
          actions={
            <button className="primary" disabled={adding || count >= (pk.data?.max ?? 5)} onClick={add}>
              {adding ? 'Follow your device’s prompt…' : 'Add passkey'}
            </button>
          }
        >
          {pk.error ? <ErrorNote error={pk.error} onRetry={pk.reload} /> : null}
          {!pk.data ? (
            <Loading />
          ) : (
            <Table
              head={['Name', 'Added', 'Last used', 'Synced', 'Transports', '']}
              rows={pk.data.passkeys.map((p) => [
                p.name,
                when(p.created_at),
                ago(p.last_used_at),
                p.backed_up ? 'yes' : 'no',
                p.transports.join(', ') || '—',
                <div className="row">
                  <button onClick={() => rename(p)}>Rename</button>
                  <button
                    className="danger"
                    disabled={busy || count <= 1}
                    title={count <= 1 ? 'You cannot remove your only passkey' : undefined}
                    onClick={() =>
                      run(
                        { title: 'Remove passkey', body: `Remove “${p.name}”? Sessions opened with it are signed out.`, danger: true, stepUp: true, confirmLabel: 'Remove' },
                        () => api.post(`/api/security/passkeys/${encodeURIComponent(p.id)}/delete`),
                        () => pk.reload(),
                        'Passkey removed.',
                      )
                    }
                  >
                    Remove
                  </button>
                </div>,
              ])}
            />
          )}
          <p className="small muted">Adding a passkey asks for your existing passkey first. The last passkey can never be removed.</p>
        </Card>

        <Card
          title="Active sessions"
          className="span-8"
          actions={
            <button
              disabled={busy}
              onClick={() =>
                run({ title: 'Sign out everywhere else', body: 'End every session except this one?', confirmLabel: 'Sign out others' }, () => api.post('/api/security/sessions/revoke-others'), () => ss.reload())
              }
            >
              Sign out other sessions
            </button>
          }
        >
          {!ss.data ? (
            <Loading />
          ) : (
            <Table
              head={['Device', 'Country', 'Signed in', 'Last active', 'Expires', '']}
              rows={ss.data.sessions.map((s) => [
                <span>
                  {s.client} {s.current ? <span className="badge plain lv-ok">this one</span> : null} {s.kind !== 'full' ? <span className="badge plain">{s.kind}</span> : null}
                </span>,
                s.country || '—',
                when(s.created_at),
                ago(s.last_seen_at),
                when(s.expires_at),
                <button
                  disabled={busy}
                  onClick={() =>
                    run(
                      { title: 'Revoke session', body: s.current ? 'This signs you out here.' : `Sign out ${s.client}?`, confirmLabel: 'Revoke' },
                      () => api.post(`/api/security/sessions/${s.id}/revoke`),
                      () => (s.current ? window.location.assign('/login') : ss.reload()),
                    )
                  }
                >
                  Revoke
                </button>,
              ])}
            />
          )}
          <p className="small muted">Sessions end after 1 hour without activity, and always 8 hours after sign-in.</p>
        </Card>

        <Card title="Recovery codes" className="span-4">
          {rc.data ? (
            <p>
              <strong className="num">{rc.data.remaining}</strong> of {rc.data.total} unused.
            </p>
          ) : (
            <Loading />
          )}
          <p className="small muted">A recovery code signs you in only far enough to register a new passkey. Each works once.</p>
          <button className="danger" onClick={regenerate}>
            Regenerate…
          </button>
          {codes ? (
            <div style={{ marginTop: 12 }}>
              <CodesBlock codes={codes} />
              <button style={{ marginTop: 8 }} onClick={() => setCodes(null)}>
                I saved them — hide
              </button>
            </div>
          ) : null}
        </Card>
      </div>
    </>
  );
}
