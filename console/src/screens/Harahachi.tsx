import { useEffect, useState } from 'react';
import type { FormEvent } from 'react';
import { api } from '../api';
import { HBars, LineChart, StackedBars } from '../components/Charts';
import { Card, ErrorNote, Kpi, Loading, Table, Tabs, UpstreamNote, useAction, useApi } from '../components/ui';
import { ago, compact, n, pct, ratio, sum, when } from '../fmt';
import type { HhFunnel, HhRetention, HhSummary, HhTimeseries, HhUserDetail, HhUserRow, Proxied } from '../types';

type Tab = 'users' | 'growth' | 'food' | 'ai' | 'reliability' | 'devices' | 'pairing';
const TABS: Array<[Tab, string]> = [
  ['users', 'Users'],
  ['growth', 'Growth'],
  ['food', 'Food'],
  ['ai', 'AI'],
  ['reliability', 'Reliability'],
  ['devices', 'Devices'],
  ['pairing', 'Pairing'],
];
const SLOTS = ['analyze', 'body', 'chat', 'insights', 'passthrough'];

function useProxied<T>(path: string | null, refreshMs = 0) {
  const r = useApi<Proxied<T>>(path, refreshMs);
  return { ...r, value: r.data?.data ?? null, upstream: r.data?.upstream, message: r.data?.message };
}

function short(days: string[]): string[] {
  return days.map((d) => d.slice(5));
}

export function Harahachi() {
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(window.location.search).get('tab') as Tab) || 'users');
  useEffect(() => {
    const u = new URL(window.location.href);
    u.searchParams.set('tab', tab);
    window.history.replaceState(null, '', u.pathname + u.search);
  }, [tab]);
  return (
    <>
      <div className="page-head">
        <h1>Harahachi</h1>
        <span className="small muted">Calorie tracker · data via the ops API, cached ≤ 45 s</span>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Harahachi sections" />
      {tab === 'users' && <Users />}
      {tab === 'growth' && <Growth />}
      {tab === 'food' && <Food />}
      {tab === 'ai' && <Ai />}
      {tab === 'reliability' && <Reliability />}
      {tab === 'devices' && <Devices />}
      {tab === 'pairing' && <Pairing />}
    </>
  );
}

// ---- Users ------------------------------------------------------------------------

function Users() {
  const [q, setQ] = useState('');
  const [query, setQuery] = useState('');
  const [sort, setSort] = useState('');
  const [page, setPage] = useState(1);
  const [open, setOpen] = useState<string | number | null>(null);
  const params = new URLSearchParams({ page: String(page) });
  if (query) params.set('q', query);
  if (sort) params.set('sort', sort);
  const r = useProxied<{ users: HhUserRow[]; total: number }>(`/api/harahachi/users?${params.toString()}`);
  if (open !== null) return <UserDetail id={open} onBack={() => setOpen(null)} />;
  const submit = (e: FormEvent) => {
    e.preventDefault();
    setPage(1);
    setQuery(q.trim());
  };
  const users = r.value?.users ?? [];
  const total = r.value?.total ?? 0;
  return (
    <Card
      title={`Users${r.value ? ` · ${n(total)}` : ''}`}
      actions={
        <form className="row" onSubmit={submit} role="search">
          <label className="sr-only" htmlFor="user-q">
            Search users
          </label>
          <input id="user-q" placeholder="Search username" value={q} onChange={(e) => setQ(e.target.value)} maxLength={64} />
          <label className="sr-only" htmlFor="user-sort">
            Sort
          </label>
          <select id="user-sort" value={sort} onChange={(e) => setSort(e.target.value)}>
            <option value="">Newest</option>
            <option value="last_active">Last active</option>
            <option value="meals">Most meals</option>
            <option value="streak_current">Longest streak</option>
            <option value="ai_calls_7d">AI calls (7 d)</option>
          </select>
          <button type="submit">Search</button>
        </form>
      }
    >
      <UpstreamNote app="Harahachi" up={r.upstream} message={r.message} />
      {r.error ? <ErrorNote error={r.error} onRetry={r.reload} /> : null}
      {!r.data && !r.error ? <Loading /> : null}
      {r.value ? (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th scope="col">User</th>
                <th scope="col">Joined</th>
                <th scope="col">Last active</th>
                <th scope="col" className="r">Meals</th>
                <th scope="col" className="r">Streak</th>
                <th scope="col">Client</th>
                <th scope="col" className="r">AI 7 d</th>
                <th scope="col">Flags</th>
              </tr>
            </thead>
            <tbody>
              {users.map((u) => (
                <tr key={u.id} className="clickable" onClick={() => setOpen(u.id)}>
                  <td>
                    <button className="link" onClick={(e) => { e.stopPropagation(); setOpen(u.id); }}>
                      {u.username}
                    </button>
                  </td>
                  <td>{when(u.created_at)}</td>
                  <td>{ago(u.last_active)}</td>
                  <td className="r">{n(u.meals)}</td>
                  <td className="r">{n(u.streak_current)}</td>
                  <td>{u.client_kind ?? '—'}</td>
                  <td className="r">{n(u.ai_calls_7d)}</td>
                  <td>
                    {u.is_admin ? <span className="badge plain">keeper</span> : null} {u.locked ? <span className="badge plain lv-crit">locked</span> : null}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {users.length === 0 ? <p className="muted small">No users match.</p> : null}
          <div className="row" style={{ marginTop: 10 }}>
            <button disabled={page <= 1} onClick={() => setPage((p) => p - 1)}>
              Previous
            </button>
            <span className="small muted num">Page {page}</span>
            <button disabled={users.length === 0 || page * 25 >= total} onClick={() => setPage((p) => p + 1)}>
              Next
            </button>
          </div>
        </div>
      ) : null}
    </Card>
  );
}

function KeyValues({ obj }: { obj: Record<string, unknown> | undefined }) {
  if (!obj || Object.keys(obj).length === 0) return <p className="muted small">—</p>;
  return (
    <Table
      head={['Field', 'Value']}
      rows={Object.entries(obj).map(([k, v]) => [k.replace(/_/g, ' '), typeof v === 'object' && v !== null ? JSON.stringify(v) : String(v)])}
    />
  );
}

function UserDetail({ id, onBack }: { id: string | number; onBack: () => void }) {
  const r = useProxied<HhUserDetail>(`/api/harahachi/users/${encodeURIComponent(String(id))}`);
  const { run, busy } = useAction();
  const d = r.value;
  const user = d?.user ?? {};
  const act = (action: string, params: Record<string, unknown>, title: string, body: string, destructive = false) =>
    run({ title, body, danger: destructive, stepUp: destructive, confirmLabel: title }, () => api.post('/api/harahachi/actions', { action, ...params }), () => r.reload());
  return (
    <div className="stack">
      <div className="row">
        <button onClick={onBack}>← All users</button>
        <h2 style={{ margin: 0 }}>{String(user.username ?? `User ${id}`)}</h2>
        <span className="spacer" />
        <button disabled={busy || !d} onClick={() => act('unlock_user', { user_id: id }, 'Unlock user', 'Clear failed PIN attempts and the lock on this account?')}>
          Unlock
        </button>
        {user.is_admin ? (
          <button className="danger" disabled={busy} onClick={() => act('revoke_keeper', { user_id: id }, 'Revoke keeper', 'Remove checkpoint-keeper (admin) rights from this user?', true)}>
            Revoke keeper
          </button>
        ) : (
          <button disabled={busy || !d} onClick={() => act('grant_keeper', { user_id: id }, 'Grant keeper', 'Make this user a checkpoint keeper (admin in the app)?')}>
            Grant keeper
          </button>
        )}
        <button disabled={busy || !d} onClick={() => act('push_test', { user_id: id }, 'Send test push', 'Send a test push notification to this user’s devices?')}>
          Test push
        </button>
      </div>
      <UpstreamNote app="Harahachi" up={r.upstream} message={r.message} />
      {r.error ? <ErrorNote error={r.error} onRetry={r.reload} /> : null}
      {!d && !r.error ? <Loading /> : null}
      {d ? (
        <div className="grid">
          <Card title="Account" className="span-4">
            <KeyValues obj={user} />
          </Card>
          <Card title="Profile & targets" className="span-4">
            <KeyValues obj={{ ...(d.profile ?? {}), ...(d.targets ?? {}) }} />
          </Card>
          <Card title="Body fat" className="span-4">
            <KeyValues obj={d.body_fat} />
          </Card>
          <Card title="Devices" className="span-12">
            <Table
              head={['Device', 'Client', 'Version', 'Last seen', 'State', '']}
              rows={(d.devices ?? []).map((dev) => [
                <span className="mono">{String(dev.id)}</span>,
                dev.client_kind ?? '—',
                dev.client_version ?? '—',
                ago(dev.last_seen),
                dev.revoked ? 'revoked' : 'active',
                dev.revoked ? '' : (
                  <button className="danger" disabled={busy} onClick={() => act('revoke_device', { device_id: dev.id }, 'Revoke device', 'This device will be signed out of Harahachi and must be paired again.', true)}>
                    Revoke
                  </button>
                ),
              ])}
            />
          </Card>
          <Card title="Recent meals (30)" className="span-6">
            <Table
              head={['When', 'Dish', 'kcal', 'Source']}
              right={[2]}
              rows={(d.meals ?? []).map((m) => [when(String(m.at ?? m.logged_at ?? '')), String(m.name ?? m.dish ?? '—'), n(Number(m.kcal ?? 0)), String(m.source ?? '—')])}
            />
          </Card>
          <Card title="AI usage by slot (30 d)" className="span-6">
            <Table
              head={['Slot', 'Calls', 'Errors', 'Tokens in', 'Tokens out']}
              right={[1, 2, 3, 4]}
              rows={Object.entries(d.ai_usage ?? {}).map(([slot, s]) => [slot, n(s.calls), n(s.errors), compact(s.tokens_in), compact(s.tokens_out)])}
            />
            <h3 style={{ marginTop: 14 }}>Weights</h3>
            <Table head={['Day', 'kg']} right={[1]} rows={(d.weights ?? []).map((w) => [String(w.day ?? w.at ?? ''), String(w.kg ?? w.weight_kg ?? '')])} />
            <h3 style={{ marginTop: 14 }}>Workouts</h3>
            <Table
              head={['When', 'Kind', 'Minutes', 'kcal']}
              right={[2, 3]}
              rows={(d.workouts ?? []).map((w) => [when(String(w.at ?? '')), String(w.kind ?? '—'), String(w.minutes ?? '—'), String(w.kcal ?? '—')])}
            />
          </Card>
        </div>
      ) : null}
    </div>
  );
}

// ---- Growth -------------------------------------------------------------------------

function avg7(xs: number[]): number[] {
  return xs.map((_, i) => {
    const w = xs.slice(Math.max(0, i - 6), i + 1);
    return w.reduce((a, b) => a + b, 0) / w.length;
  });
}

function Growth() {
  const ts = useProxied<HhTimeseries>('/api/harahachi/timeseries?days=60');
  const ret = useProxied<HhRetention>('/api/harahachi/retention?weeks=8');
  const fun = useProxied<HhFunnel>('/api/harahachi/funnel');
  const s = ts.value?.series ?? {};
  const days = short(ts.value?.days ?? []);
  const dau = s.dau ?? [];
  const funnel = fun.value;
  const steps: Array<[string, number]> = funnel
    ? [
        ['Paired devices', funnel.paired_devices ?? 0],
        ['Registered users', funnel.registered_users ?? 0],
        ['Logged a first meal', funnel.first_meal ?? 0],
        ['Active 3 days', funnel.active_3_days ?? 0],
        ['Active 7 days', funnel.active_7_days ?? 0],
      ]
    : [];
  return (
    <div className="grid">
      <Card title="Signups per day (60 d)" className="span-6">
        <UpstreamNote app="Harahachi" up={ts.upstream} message={ts.message} />
        {ts.value ? <LineChart title="New users per day" labels={days} series={[{ name: 'New users', values: s.new_users ?? [] }]} area /> : <Loading />}
        <p className="small muted">{n(sum(s.new_users))} signups in the window.</p>
      </Card>
      <Card title="Active users" className="span-6">
        {ts.value ? (
          <LineChart
            title="Daily active users"
            labels={days}
            series={[
              { name: 'DAU', values: dau },
              { name: 'DAU, 7-day average', values: avg7(dau), color: 'var(--s2)', dashed: true },
              ...(s.wau ? [{ name: 'WAU', values: s.wau, color: 'var(--s3)' }] : []),
              ...(s.mau ? [{ name: 'MAU', values: s.mau, color: 'var(--s4)' }] : []),
            ]}
          />
        ) : (
          <Loading />
        )}
        {ts.value && !s.wau ? (
          <p className="small muted">WAU / MAU need distinct-user counts per window, which the ops API's timeseries does not include yet; DAU is shown.</p>
        ) : null}
      </Card>
      <Card title="Weekly signup cohorts — % active in week N" className="span-8">
        <UpstreamNote app="Harahachi" up={ret.upstream} message={ret.message} />
        {ret.value?.cohorts?.length ? (
          <div className="table-wrap">
            <table className="cohort">
              <thead>
                <tr>
                  <th scope="col">Cohort</th>
                  <th scope="col" className="r">Users</th>
                  {Array.from({ length: Math.max(...ret.value.cohorts.map((c) => c.active_pct.length)) }, (_, i) => (
                    <th scope="col" key={i}>
                      W{i + 1}
                    </th>
                  ))}
                </tr>
              </thead>
              <tbody>
                {ret.value.cohorts.map((c) => (
                  <tr key={c.week}>
                    <td style={{ textAlign: 'left' }}>{c.week}</td>
                    <td className="r">{c.size}</td>
                    {c.active_pct.map((p, i) => (
                      <td key={i} style={{ background: `color-mix(in srgb, var(--accent) ${Math.round(Math.min(100, p) * 0.6)}%, transparent)` }}>
                        {Math.round(p)}
                      </td>
                    ))}
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        ) : ret.value ? (
          <p className="muted small">No cohorts yet.</p>
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Funnel" className="span-4">
        <UpstreamNote app="Harahachi" up={fun.upstream} message={fun.message} />
        {funnel ? (
          <>
            <HBars rows={steps} />
            <p className="small muted">
              Pair → first meal: {ratio(funnel.first_meal, funnel.paired_devices)} · first meal → active 7 d: {ratio(funnel.active_7_days, funnel.first_meal)}
            </p>
          </>
        ) : (
          <Loading />
        )}
      </Card>
    </div>
  );
}

// ---- Food -----------------------------------------------------------------------------

function Food() {
  const ts = useProxied<HhTimeseries>('/api/harahachi/timeseries?days=30');
  const sm = useProxied<HhSummary>('/api/harahachi/summary');
  const s = ts.value?.series ?? {};
  const sources: Array<[string, string]> = [
    ['meals_photo', 'Photo'],
    ['meals_barcode', 'Barcode'],
    ['meals_manual', 'Manual'],
    ['meals_chat', 'Chat (Annai)'],
    ['meals_quicklog', 'Quick-log'],
  ];
  return (
    <div className="grid">
      <Card title="Meals by source (30 d)" className="span-8">
        <UpstreamNote app="Harahachi" up={ts.upstream} message={ts.message} />
        {ts.value ? (
          <StackedBars title="Meals per day by source" labels={short(ts.value.days)} series={sources.map(([k, name]) => ({ name, values: s[k] ?? [] }))} />
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Share by source (30 d)" className="span-4">
        {ts.value ? <HBars rows={sources.map(([k, name]) => [name, sum(s[k])] as [string, number]).sort((a, b) => b[1] - a[1])} /> : <Loading />}
        <p className="small muted">{n(sum(s.meals))} meals logged in total.</p>
      </Card>
      <Card title="Top logged dishes" className="span-12">
        {sm.value?.top_dishes?.length ? (
          <HBars rows={sm.value.top_dishes.map((d) => [d.name, d.count] as [string, number])} />
        ) : (
          <p className="muted small">The ops API contract has no dish aggregate yet (a <span className="mono">top_dishes</span> field on /summary would appear here).</p>
        )}
      </Card>
    </div>
  );
}

// ---- AI --------------------------------------------------------------------------------

function Ai() {
  const sm = useProxied<HhSummary>('/api/harahachi/summary', 60_000);
  const ts = useProxied<HhTimeseries>('/api/harahachi/timeseries?days=30');
  const { run, busy } = useAction();
  const [slot, setSlot] = useState('analyze');
  const [model, setModel] = useState('');
  const [fallback, setFallback] = useState('');
  const slots = sm.value?.ai_by_slot ?? {};
  const s = ts.value?.series ?? {};
  const days = short(ts.value?.days ?? []);
  const submit = (e: FormEvent) => {
    e.preventDefault();
    const m = model.trim();
    if (!m) return;
    run(
      { title: 'Switch model', body: `Set the ${slot} slot to "${m}"${fallback.trim() ? ` with fallback "${fallback.trim()}"` : ''}? This changes live AI behaviour for every user.`, confirmLabel: 'Switch' },
      () => api.post('/api/harahachi/actions', { action: 'set_model', slot, model: m, ...(fallback.trim() ? { fallback: fallback.trim() } : {}) }),
      () => sm.reload(),
      'Model switched.',
    );
  };
  return (
    <div className="grid">
      <Card title="Slots, last 24 h" className="span-12">
        <UpstreamNote app="Harahachi" up={sm.upstream} message={sm.message} />
        {sm.value ? (
          <Table
            head={['Slot', 'Model', 'Calls', 'Errors', 'Error rate', 'p50', 'p95', 'Tokens in', 'Tokens out']}
            right={[2, 3, 4, 5, 6, 7, 8]}
            rows={SLOTS.concat(Object.keys(slots).filter((k) => !SLOTS.includes(k))).map((k) => {
              const v = slots[k] ?? {};
              return [
                k,
                v.model ? <span className="mono">{v.model}</span> : <span className="muted">not reported</span>,
                n(v.calls),
                n(v.errors),
                ratio(v.errors, v.calls),
                v.p50_ms != null ? `${n(v.p50_ms)} ms` : '—',
                v.p95_ms != null ? `${n(v.p95_ms)} ms` : '—',
                compact(v.tokens_in),
                compact(v.tokens_out),
              ];
            })}
          />
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Calls and errors (30 d)" className="span-6">
        {ts.value ? (
          <LineChart title="AI calls and errors per day" labels={days} series={[{ name: 'Calls', values: s.ai_calls ?? [] }, { name: 'Errors', values: s.ai_errors ?? [], color: 'var(--crit)' }]} />
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Token burn (30 d)" className="span-6">
        {ts.value ? (
          <LineChart title="Tokens per day" labels={days} series={[{ name: 'Tokens in', values: s.ai_tokens_in ?? [] }, { name: 'Tokens out', values: s.ai_tokens_out ?? [], color: 'var(--s3)' }]} />
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Daily cap hits (30 d)" className="span-6">
        {ts.value ? <LineChart title="Cap hits per day" labels={days} series={[{ name: 'Cap hits', values: s.cap_hits ?? [], color: 'var(--warn)' }]} /> : <Loading />}
        <p className="small muted">{n(sum(s.cap_hits))} requests were refused by the daily cap in 30 days.</p>
      </Card>
      <Card title="Switch model for a slot" className="span-6">
        <form className="stack" onSubmit={submit}>
          <div className="row">
            <label>
              Slot
              <select value={slot} onChange={(e) => setSlot(e.target.value)}>
                {SLOTS.map((x) => (
                  <option key={x}>{x}</option>
                ))}
              </select>
            </label>
            <label style={{ flex: 1 }}>
              Model id
              <input value={model} onChange={(e) => setModel(e.target.value)} placeholder="e.g. meta/llama-3.3-70b-instruct" maxLength={120} required />
            </label>
            <label style={{ flex: 1 }}>
              Fallback (optional)
              <input value={fallback} onChange={(e) => setFallback(e.target.value)} maxLength={120} />
            </label>
          </div>
          <div>
            <button type="submit" disabled={busy || !model.trim()}>
              Switch…
            </button>
          </div>
        </form>
      </Card>
    </div>
  );
}

// ---- Reliability ---------------------------------------------------------------------------

interface ReqRow {
  at?: string;
  method?: string;
  route?: string;
  path?: string;
  status?: number;
  ms?: number;
  client?: string;
  [k: string]: unknown;
}

function Reliability() {
  const ts = useProxied<HhTimeseries>('/api/harahachi/timeseries?days=30');
  const [status, setStatus] = useState('5xx');
  const [route, setRoute] = useState('');
  const qs = new URLSearchParams({ limit: '200' });
  if (status) qs.set('status', status);
  if (route) qs.set('route', route);
  const req = useProxied<{ rows?: ReqRow[]; requests?: ReqRow[] } | ReqRow[]>(`/api/harahachi/requests?${qs.toString()}`);
  const s = ts.value?.series ?? {};
  const days = short(ts.value?.days ?? []);
  const rows: ReqRow[] = Array.isArray(req.value) ? req.value : (req.value?.rows ?? req.value?.requests ?? []);
  const reqs = sum(s.requests);
  const e5 = sum(s.errors_5xx);
  return (
    <div className="grid">
      <Card title="Requests and 5xx (30 d)" className="span-6">
        <UpstreamNote app="Harahachi" up={ts.upstream} message={ts.message} />
        {ts.value ? (
          <LineChart title="Requests and server errors per day" labels={days} series={[{ name: 'Requests', values: s.requests ?? [] }, { name: '5xx', values: s.errors_5xx ?? [], color: 'var(--crit)' }]} />
        ) : (
          <Loading />
        )}
        <p className="small muted">
          {n(reqs)} requests · {n(e5)} server errors ({reqs ? pct((100 * e5) / reqs, 2) : '—'})
        </p>
      </Card>
      <Card title="p95 latency (30 d)" className="span-6">
        {ts.value ? <LineChart title="p95 latency per day in ms" labels={days} series={[{ name: 'p95 ms', values: s.p95_ms ?? [], color: 'var(--s3)' }]} /> : <Loading />}
        <p className="small muted">Pushes: {n(sum(s.pushes_sent))} sent · {n(sum(s.pushes_failed))} failed.</p>
      </Card>
      <Card
        title="Request log"
        className="span-12"
        actions={
          <div className="row">
            <label>
              Status
              <select value={status} onChange={(e) => setStatus(e.target.value)}>
                <option value="">All</option>
                <option value="5xx">5xx</option>
                <option value="4xx">4xx</option>
                <option value="429">429</option>
              </select>
            </label>
            <label>
              Route contains
              <input value={route} onChange={(e) => setRoute(e.target.value.replace(/[^A-Za-z0-9_/\-.]/g, ''))} maxLength={80} placeholder="/api/v1/…" />
            </label>
          </div>
        }
      >
        <UpstreamNote app="Harahachi" up={req.upstream} message={req.message} />
        <Table
          head={['When', 'Method', 'Route', 'Status', 'ms', 'Client']}
          right={[3, 4]}
          empty="No matching requests in the log."
          rows={rows.map((x) => [
            when(x.at),
            x.method ?? '',
            <span className="mono">{x.route ?? x.path ?? ''}</span>,
            <span className={x.status && x.status >= 500 ? 'lv-crit' : undefined}>{x.status ?? ''}</span>,
            n(x.ms),
            x.client ?? '',
          ])}
        />
      </Card>
    </div>
  );
}

// ---- Devices ---------------------------------------------------------------------------------

function Devices() {
  const sm = useProxied<HhSummary>('/api/harahachi/summary');
  const { run, busy } = useAction();
  const [dev, setDev] = useState('');
  const clients = sm.value?.clients ?? {};
  const totals = sm.value?.totals ?? {};
  const rows: Array<[string, number]> = Object.entries(clients).map(([k, v]) => [k.toUpperCase(), typeof v === 'number' ? v : (v.count ?? 0)]);
  // Harahachi reports versions in a separate `client_versions` field ({kind: {version: n}})
  // so `clients` keeps its plain-number contract; the nested shape is still accepted.
  const versionSource: Record<string, Record<string, number>> =
    sm.value?.client_versions ??
    Object.fromEntries(
      Object.entries(clients).flatMap(([k, v]) => (typeof v === 'object' && v.versions ? [[k, v.versions]] : [])),
    );
  const versions = Object.entries(versionSource).flatMap(([k, vers]) =>
    Object.entries(vers).map(([ver, c]) => [`${k}/${ver}`, c] as [string, number]),
  );
  return (
    <div className="grid">
      <Card title="Clients" className="span-6">
        <UpstreamNote app="Harahachi" up={sm.upstream} message={sm.message} />
        {sm.value ? (
          <>
            <HBars rows={rows} />
            <div className="kpis" style={{ marginTop: 12 }}>
              <Kpi label="Active devices" value={n(totals.devices_active)} />
              <Kpi label="Revoked devices" value={n(totals.devices_revoked)} />
              <Kpi label="Push subscriptions" value={n(totals.push_subs)} />
            </div>
          </>
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Versions" className="span-6">
        {versions.length ? (
          <HBars rows={versions.sort((a, b) => b[1] - a[1])} />
        ) : (
          <p className="muted small">No app versions reported yet. Each user's devices (with versions) are listed on their page in Users.</p>
        )}
      </Card>
      <Card title="Revoke a device by id" className="span-12">
        <form
          className="row"
          onSubmit={(e) => {
            e.preventDefault();
            const id = dev.trim();
            if (!id) return;
            run(
              { title: 'Revoke device', body: `Revoke device "${id}"? It will have to be paired again.`, danger: true, stepUp: true, confirmLabel: 'Revoke' },
              () => api.post('/api/harahachi/actions', { action: 'revoke_device', device_id: id }),
              () => setDev(''),
              'Device revoked.',
            );
          }}
        >
          <label>
            Device id
            <input value={dev} onChange={(e) => setDev(e.target.value)} maxLength={80} />
          </label>
          <button className="danger" type="submit" disabled={busy || !dev.trim()} style={{ alignSelf: 'flex-end' }}>
            Revoke…
          </button>
        </form>
      </Card>
    </div>
  );
}

// ---- Pairing -------------------------------------------------------------------------------------

interface Pairing {
  token: string;
  url: string;
  qr_png_base64: string;
  expires_in: number;
}

function Pairing() {
  const { run, busy } = useAction();
  const [p, setP] = useState<{ v: Pairing; at: number } | null>(null);
  const [now, setNow] = useState(Date.now());
  useEffect(() => {
    if (!p) return;
    const t = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(t);
  }, [p]);
  const left = p ? Math.max(0, Math.round(p.v.expires_in - (now - p.at) / 1000)) : 0;
  return (
    <Card title="Pair a new device">
      <p className="muted">Generates a single-use pairing token on Harahachi. Show the QR to the phone you want to pair.</p>
      <button
        className="primary"
        disabled={busy}
        onClick={() =>
          run(
            { title: 'Generate pairing QR', body: 'Anyone who scans this QR before it expires can pair a device. Generate it?', confirmLabel: 'Generate' },
            () => api.post<Proxied<Pairing>>('/api/harahachi/actions', { action: 'pairing_token' }),
            (r) => r.data && setP({ v: r.data, at: Date.now() }),
            'Pairing token created.',
          )
        }
      >
        Generate pairing QR…
      </button>
      {p && left > 0 ? (
        <div className="row" style={{ marginTop: 16, alignItems: 'flex-start' }}>
          <img className="qr" alt="Pairing QR code" src={`data:image/png;base64,${p.v.qr_png_base64}`} />
          <div className="stack">
            <div>
              Expires in <strong className="num">{Math.floor(left / 60)}:{String(left % 60).padStart(2, '0')}</strong>
            </div>
            <div className="mono" style={{ wordBreak: 'break-all' }}>
              {p.v.url}
            </div>
            <button onClick={() => setP(null)}>Hide</button>
          </div>
        </div>
      ) : p ? (
        <p className="muted">That token has expired.</p>
      ) : null}
    </Card>
  );
}
