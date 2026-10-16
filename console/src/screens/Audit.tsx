import { useState } from 'react';
import { Card, ErrorNote, Loading, Table, Tabs, UpstreamNote, useApi } from '../components/ui';
import { when } from '../fmt';
import type { AuditRow, Proxied } from '../types';

type Src = 'console' | 'harahachi' | 'busboard';

export function Audit() {
  const [src, setSrc] = useState<Src>('console');
  return (
    <>
      <div className="page-head">
        <h1>Audit</h1>
        <span className="small muted">Every console sign-in and action, plus each app's own ops audit</span>
      </div>
      <Tabs
        tabs={[
          ['console', 'Console'],
          ['harahachi', 'Harahachi ops'],
          ['busboard', 'Bus Board ops'],
        ]}
        value={src}
        onChange={setSrc}
        label="Audit source"
      />
      {src === 'console' ? <ConsoleAudit /> : <AppAudit src={src} />}
    </>
  );
}

function ConsoleAudit() {
  const [filter, setFilter] = useState('all');
  const { data, error, reload } = useApi<{ rows: AuditRow[] }>('/api/audit/console?limit=500', 60_000);
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const rows = data.rows.filter((r) =>
    filter === 'all' ? true : filter === 'auth' ? /^(login|logout|setup|recovery|step_up|session|passkey|dev)/.test(r.action) : filter === 'fail' ? r.outcome !== 'ok' : !r.action.startsWith('collect'),
  );
  return (
    <Card
      title={`Console audit · ${rows.length}`}
      actions={
        <label>
          Show
          <select value={filter} onChange={(e) => setFilter(e.target.value)}>
            <option value="all">Everything</option>
            <option value="auth">Sign-ins & security</option>
            <option value="fail">Failures only</option>
            <option value="nocron">Hide collector runs</option>
          </select>
        </label>
      }
    >
      <Table
        head={['When', 'Actor', 'Action', 'Target', 'Outcome', 'Country', 'Client', 'IP (daily hash)']}
        rows={rows.map((r) => [
          when(r.at),
          r.actor,
          <span className="mono">{r.action}</span>,
          <span className="mono">{r.target}</span>,
          <span className={r.outcome === 'ok' || r.outcome === 'up' ? 'lv-ok' : 'lv-crit'}>{r.outcome}</span>,
          r.country || '—',
          r.client || '—',
          <span className="mono">{r.ip_hash || '—'}</span>,
        ])}
      />
    </Card>
  );
}

function AppAudit({ src }: { src: 'harahachi' | 'busboard' }) {
  const app = src === 'harahachi' ? 'Harahachi' : 'Bus Board';
  const { data, error, reload } = useApi<Proxied<{ rows?: Array<Record<string, unknown>> } | Array<Record<string, unknown>>>>(`/api/audit/${src}?limit=200`);
  if (error) return <ErrorNote error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const v = data.data;
  const rows = Array.isArray(v) ? v : (v?.rows ?? []);
  return (
    <Card title={`${app} ops audit`}>
      {src === 'busboard' && !data.upstream.ok ? (
        <div className="note">Bus Board's ops API contract defines no audit read endpoint (GET /v1/ops/audit), so its own audit rows cannot be shown here. Console-side Bus Board actions are in the Console tab.</div>
      ) : (
        <UpstreamNote app={app} up={data.upstream} message={data.message} />
      )}
      {data.upstream.ok ? (
        <Table
          head={['When', 'Action', 'Target', 'Params', 'Result']}
          empty="No ops actions recorded yet."
          rows={rows.map((r) => [
            when(String(r.at ?? '')),
            <span className="mono">{String(r.action ?? '')}</span>,
            <span className="mono">{String(r.target ?? '')}</span>,
            <span className="mono small">{String(r.params_json ?? '')}</span>,
            String(r.result ?? ''),
          ])}
        />
      ) : null}
    </Card>
  );
}
