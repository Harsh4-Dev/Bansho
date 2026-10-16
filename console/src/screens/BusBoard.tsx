import { useEffect, useState } from 'react';
import { api } from '../api';
import { HBars, LineChart, StatusStrip } from '../components/Charts';
import { Card, ErrorNote, Kpi, Loading, StateBadge, Table, Tabs, UpstreamNote, useAction, useApi, useFeedback } from '../components/ui';
import { ago, duration, n, pct, when } from '../fmt';
import type { BbLive, BbStats, BbTraffic } from '../types';

type Tab = 'live' | 'traffic' | 'demand' | 'contributors' | 'feed';
const TABS: Array<[Tab, string]> = [
  ['live', 'Live'],
  ['traffic', 'Traffic'],
  ['demand', 'Demand'],
  ['contributors', 'Contributors'],
  ['feed', 'Feed'],
];

export function BusBoard() {
  const [tab, setTab] = useState<Tab>(() => (new URLSearchParams(window.location.search).get('tab') as Tab) || 'live');
  useEffect(() => {
    const u = new URL(window.location.href);
    u.searchParams.set('tab', tab);
    window.history.replaceState(null, '', u.pathname + u.search);
  }, [tab]);
  const live = useApi<BbLive>('/api/busboard/live', 60_000);
  const { toast } = useFeedback();
  const [waking, setWaking] = useState(false);
  const wake = async () => {
    setWaking(true);
    try {
      const r = await api.post<{ state: string }>('/api/busboard/wake');
      toast(r.state === 'up' ? 'Bus Board is awake.' : `Still ${r.state}. Render can take up to a minute; try again shortly.`);
      live.reload();
    } catch (e) {
      toast(e instanceof Error ? e.message : 'Wake failed');
    } finally {
      setWaking(false);
    }
  };
  const d = live.data;
  return (
    <>
      <div className="page-head">
        <h1>Bus Board</h1>
        <div className="row">
          {d ? <StateBadge state={d.state} /> : null}
          {d && d.state !== 'up' ? (
            <button className="primary" onClick={wake} disabled={waking}>
              {waking ? 'Waking… (up to a minute)' : 'Wake it'}
            </button>
          ) : null}
        </div>
      </div>
      <Tabs tabs={TABS} value={tab} onChange={setTab} label="Bus Board sections" />
      {live.error && !d ? <ErrorNote error={live.error} onRetry={live.reload} /> : null}
      {!d && !live.error ? <Loading /> : null}
      {d ? (
        <>
          {d.state === 'asleep' ? (
            <div className="note" style={{ marginBottom: 12 }}>
              Bus Board is <strong>asleep</strong> — normal on Render's free tier after 15 idle minutes. Its counters live in memory, so
              figures below are from the last snapshot ({ago(d.as_of)}). “Wake it” just calls its health endpoint.
            </div>
          ) : (
            <UpstreamNote app="Bus Board" up={d.upstream} asleepOk />
          )}
          {tab === 'live' && <Live d={d} />}
          {tab === 'traffic' && <Traffic />}
          {tab === 'demand' && <Demand s={d.stats} />}
          {tab === 'contributors' && <Contributors s={d.stats} reload={live.reload} />}
          {tab === 'feed' && <Feed s={d.stats} />}
        </>
      ) : null}
    </>
  );
}

function Timeline({ d }: { d: BbLive }) {
  const end = Date.now();
  const start = end - 7 * 86400_000;
  const x = (iso: string) => Math.max(0, Math.min(100, ((new Date(iso).getTime() - start) / (end - start)) * 100));
  const boots = d.timeline.boots.filter((b) => new Date(b.last_seen_at).getTime() >= start);
  const events = d.timeline.events.filter((e) => new Date(e.at).getTime() >= start);
  return (
    <div>
      <div className="timeline" role="img" aria-label={`${boots.length} process lifetimes and ${events.length} restarts or wakes in the last 7 days`}>
        {boots.map((b) => (
          <span
            key={b.boot_id}
            className="boot"
            style={{ left: `${x(b.started_at)}%`, width: `${Math.max(0.3, x(b.last_seen_at) - x(b.started_at))}%` }}
            title={`boot ${b.boot_id}: ${when(b.started_at)} → ${when(b.last_seen_at)}`}
          />
        ))}
        {events.map((e) => (
          <span key={`${e.at}-${e.kind}`} className={`mark ${e.kind}`} style={{ left: `${x(e.at)}%` }} title={`${e.kind} ${when(e.at)}`} />
        ))}
      </div>
      <div className="row small muted" style={{ justifyContent: 'space-between', marginTop: 4 }}>
        <span>7 days ago</span>
        <span>
          <span className="lv-ok">■</span> process alive · <span className="st-asleep">|</span> wake after sleep · <span className="lv-crit">|</span> restart
        </span>
        <span>now</span>
      </div>
    </div>
  );
}

function Live({ d }: { d: BbLive }) {
  const s = d.stats;
  const restarts = d.timeline.events.filter((e) => e.kind === 'restart').length;
  const wakes = d.timeline.events.filter((e) => e.kind === 'wake').length;
  return (
    <div className="grid">
      <Card title={d.source === 'live' ? 'Right now' : `Last snapshot · ${ago(d.as_of)}`} className="span-7">
        {s ? (
          <div className="kpis">
            <Kpi label="Up for" value={duration(s.uptime_s)} sub={`since ${when(typeof s.started_at === 'string' ? s.started_at : undefined)}`} />
            <Kpi label="Live streams" value={n(s.sse?.open)} sub={`${n(s.sse?.total_opened)} opened since boot`} />
            <Kpi label="Active vehicles" value={n(s.vehicles?.active)} />
            <Kpi label="Requests today" value={n(d.today.requests)} sub={`${n(d.today.errors_5xx)} × 5xx`} />
            <Kpi label="Pings today" value={n(d.today.pings_received)} sub={`${n(d.today.pings_accepted)} accepted`} />
            <Kpi label="Version" value={<span style={{ fontSize: 15 }}>{s.version ?? '—'}</span>} sub={<span className="mono">{(s.boot_id ?? '').slice(0, 12)}</span>} />
          </div>
        ) : (
          <p className="muted">No Bus Board data has been collected yet.</p>
        )}
        {s?.vehicles?.by_route ? (
          <>
            <h3 style={{ marginTop: 14 }}>Vehicles by route</h3>
            <HBars rows={Object.entries(s.vehicles.by_route)} empty="No vehicles on the road." />
          </>
        ) : null}
      </Card>
      <Card title="Process" className="span-5">
        {s?.runtime ? (
          <div className="kpis">
            <Kpi label="Goroutines" value={n(s.runtime.goroutines)} />
            <Kpi label="Heap" value={`${s.runtime.heap_mb ?? '—'} MB`} />
            <Kpi label="Go" value={<span style={{ fontSize: 15 }}>{s.runtime.go_version ?? '—'}</span>} />
          </div>
        ) : (
          <p className="muted small">—</p>
        )}
        <h3 style={{ marginTop: 14 }}>Availability</h3>
        <p className="small" style={{ margin: 0 }}>
          Up {pct(d.uptime_24h.pct_up)} · available (up or asleep) {pct(d.uptime_24h.pct_available)} over 24 h; {pct(d.uptime_30d.pct_available)} over 30 d.
        </p>
        <p className="small muted">{restarts} restarts and {wakes} wakes from sleep in 30 days.</p>
      </Card>
      <Card title="Boot timeline — sleeps, wakes and restarts (7 d)" className="span-12">
        <Timeline d={d} />
        <h3 style={{ marginTop: 14 }} className="small muted">
          Checks, last 48 h
        </h3>
        <StatusStrip checks={d.strip} label="Bus Board checks, last 48 hours" />
        <h3 style={{ marginTop: 14 }}>Recent restarts and wakes</h3>
        <Table
          head={['When', 'Kind', 'Silent before', 'New boot', 'Version']}
          empty="None recorded."
          rows={d.timeline.events
            .slice(-12)
            .reverse()
            .map((e) => [when(e.at), e.kind, duration(e.gap_s), <span className="mono">{e.boot_id ?? ''}</span>, e.version ?? ''])}
        />
      </Card>
    </div>
  );
}

function Traffic() {
  const [hours, setHours] = useState(48);
  const t = useApi<BbTraffic>(`/api/busboard/traffic?hours=${hours}`);
  const d = t.data;
  const labels = (d?.series.at ?? []).map((iso) => new Date(iso).toLocaleString(undefined, { month: 'numeric', day: 'numeric', hour: '2-digit' }));
  const num = (k: string) => ((d?.series[k] ?? []) as number[]).map(Number);
  return (
    <div className="grid">
      <Card
        title="Requests per collection interval"
        className="span-12"
        actions={
          <label>
            Window
            <select value={hours} onChange={(e) => setHours(Number(e.target.value))}>
              <option value={24}>24 h</option>
              <option value={48}>48 h</option>
              <option value={168}>7 d</option>
              <option value={720}>30 d</option>
            </select>
          </label>
        }
      >
        {t.error ? <ErrorNote error={t.error} onRetry={t.reload} /> : null}
        {d ? (
          <>
            <LineChart
              title="Bus Board requests, 4xx and 5xx per interval; dashed red lines mark restarts"
              labels={labels}
              markers={d.series.restart}
              height={180}
              series={[
                { name: 'Requests', values: num('requests') },
                { name: '4xx', values: num('errors_4xx'), color: 'var(--warn)' },
                { name: '5xx', values: num('errors_5xx'), color: 'var(--crit)' },
              ]}
            />
            <p className="small muted">
              Deltas between snapshots, computed per boot: a restart starts a new baseline instead of producing negative traffic. Dashed red lines are restarts/wakes.
            </p>
          </>
        ) : (
          <Loading />
        )}
      </Card>
      <Card title="Per route" className="span-7">
        {d ? (
          <Table
            head={['Route', 'Requests', '5xx', 'Latest p95']}
            right={[1, 2, 3]}
            rows={Object.entries(d.routes).map(([name, r]) => [
              <span className="mono">{name}</span>,
              n(r.total),
              n(r.errors_5xx.reduce((a, b) => a + b, 0)),
              `${n(r.p95_ms[r.p95_ms.length - 1] ?? 0)} ms`,
            ])}
          />
        ) : (
          <Loading />
        )}
        <p className="small muted">SSE streams are counted when opened, not timed, so their latency reads 0.</p>
      </Card>
      <Card title="Streams and pings" className="span-5">
        {d ? (
          <LineChart
            title="SSE streams opened and pings received per interval"
            labels={labels}
            series={[
              { name: 'SSE opened', values: num('sse_opened'), color: 'var(--s3)' },
              { name: 'Pings received', values: num('pings_received'), color: 'var(--s4)' },
              { name: 'Pings accepted', values: num('pings_accepted'), color: 'var(--s5)', dashed: true },
            ]}
          />
        ) : (
          <Loading />
        )}
      </Card>
    </div>
  );
}

function Demand({ s }: { s: BbStats | null }) {
  const top = s?.board_queries?.top ?? [];
  return (
    <Card title="Most asked journeys (since the current boot)">
      <HBars rows={top.map((q) => [`${q.from} → ${q.to}`, q.count] as [string, number])} empty="No board queries counted since this process started." />
      <p className="small muted">Counts reset whenever Bus Board restarts or wakes; Bus Board keeps the top 50.</p>
    </Card>
  );
}

function Contributors({ s, reload }: { s: BbStats | null; reload: () => void }) {
  const { run, busy } = useAction();
  const [manual, setManual] = useState('');
  const c = s?.contributors;
  const hist = Object.entries(c?.trust_histogram ?? {}).sort((a, b) => a[0].localeCompare(b[0]));
  const pings = s?.pings;
  const block = (id: string, op: 'block' | 'unblock') =>
    run(
      op === 'block'
        ? { title: 'Block contributor', body: `Block ${id}? Their location pings will be rejected (reason “blocked”).`, danger: true, stepUp: true, confirmLabel: 'Block' }
        : { title: 'Unblock contributor', body: `Unblock ${id}? Their pings will be judged normally again.`, confirmLabel: 'Unblock' },
      () => api.post(`/api/busboard/contributors/${encodeURIComponent(id)}/${op}`),
      () => reload(),
      op === 'block' ? 'Contributor blocked.' : 'Contributor unblocked.',
    );
  const blocked = new Set(s?.blocked ?? []);
  return (
    <div className="grid">
      <Card title={`Trust histogram · ${n(c?.count)} contributors`} className="span-5">
        <HBars rows={hist} empty="No contributors yet." />
        <h3 style={{ marginTop: 14 }}>Pings since boot</h3>
        {pings ? (
          <>
            <p className="small" style={{ margin: '0 0 6px' }}>
              {n(pings.received)} received · {n(pings.accepted)} accepted
            </p>
            <HBars rows={Object.entries(pings.rejected ?? {}).sort((a, b) => b[1] - a[1])} empty="No rejections." />
          </>
        ) : (
          <p className="muted small">—</p>
        )}
      </Card>
      <Card title="Top contributors" className="span-7">
        <Table
          head={['Contributor (hashed)', 'Trust', 'Accepted', '']}
          right={[1, 2]}
          empty="No contributors yet."
          rows={(c?.top ?? []).map((t) => {
            const isBlocked = t.blocked || blocked.has(t.id_hash);
            return [
              <span className="mono">{t.id_hash}</span>,
              t.score.toFixed(2),
              n(t.accepted),
              isBlocked ? (
                <button disabled={busy} onClick={() => block(t.id_hash, 'unblock')}>
                  Unblock
                </button>
              ) : (
                <button className="danger" disabled={busy} onClick={() => block(t.id_hash, 'block')}>
                  Block
                </button>
              ),
            ];
          })}
        />
        <form
          className="row"
          style={{ marginTop: 12 }}
          onSubmit={(e) => {
            e.preventDefault();
            if (manual.trim()) block(manual.trim(), 'block');
          }}
        >
          <label>
            Block by contributor id
            <input value={manual} onChange={(e) => setManual(e.target.value)} maxLength={80} />
          </label>
          <button className="danger" type="submit" disabled={busy || !manual.trim()} style={{ alignSelf: 'flex-end' }}>
            Block…
          </button>
        </form>
        {blocked.size ? (
          <p className="small muted">
            Blocked: {[...blocked].map((b) => (
              <span key={b} className="mono">
                {b}{' '}
              </span>
            ))}
          </p>
        ) : null}
      </Card>
    </div>
  );
}

function Feed({ s }: { s: BbStats | null }) {
  const { run, busy } = useAction();
  const f = s?.feed;
  return (
    <div className="grid">
      <Card title="Corridor feed" className="span-8">
        {f ? (
          <div className="kpis">
            <Kpi label="Stops" value={n(f.stops)} />
            <Kpi label="Unverified stops" value={n(f.unverified_stops)} sub={f.stops ? `${Math.round((100 * (f.unverified_stops ?? 0)) / f.stops)} % of stops` : undefined} bad={(f.unverified_stops ?? 0) > 0} />
            <Kpi label="Routes" value={n(f.routes)} />
            <Kpi label="Feed version" value={<span style={{ fontSize: 15 }}>{f.version ?? '—'}</span>} />
          </div>
        ) : (
          <p className="muted">No feed data yet.</p>
        )}
        <p className="small muted">Unverified stops are positions not yet surveyed on the ground (verified=no in the seed).</p>
      </Card>
      <Card title="State snapshot" className="span-4">
        <p className="small muted">Ask Bus Board to save its learned state now. Without a snapshot store configured it does nothing and says so.</p>
        <button
          disabled={busy}
          onClick={() =>
            run({ title: 'Save snapshot', body: 'Ask Bus Board to write its state snapshot now?', confirmLabel: 'Save' }, () => api.post('/api/busboard/snapshot'), undefined, 'Snapshot requested.')
          }
        >
          Save snapshot now…
        </button>
      </Card>
    </div>
  );
}
