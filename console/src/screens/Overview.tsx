import { StatusStrip } from '../components/Charts';
import { Card, ErrorNote, Kpi, LevelBadge, Loading, StateBadge, useApi } from '../components/ui';
import { ago, compact, n, pct, ratio } from '../fmt';
import type { Overview as OverviewData, UptimeStats } from '../types';
import { navigate } from '../router';

function Uptime({ u24, u30, availability = false }: { u24: UptimeStats; u30: UptimeStats; availability?: boolean }) {
  const key = availability ? 'pct_available' : 'pct_up';
  return (
    <p className="small muted" style={{ margin: '6px 0 0' }}>
      {availability ? 'Available (up or asleep)' : 'Uptime'}: <span className="num">{pct(u24[key], 2)}</span> 24 h ·{' '}
      <span className="num">{pct(u30[key], 2)}</span> 30 d · {u24.checks} checks today
    </p>
  );
}

export function Overview() {
  const { data, error, loading, reload } = useApi<OverviewData>('/api/overview', 60_000);
  if (error && !data) return <ErrorNote error={error} onRetry={reload} />;
  if (!data) return <Loading />;
  const { harahachi: hh, busboard: bb, landing } = data.apps;
  const k = hh.kpis;
  const b = bb.kpis;
  return (
    <>
      <div className="page-head">
        <h1>Overview</h1>
        <span className="small muted">
          Collector: {data.collector.last_run_at ? ago(data.collector.last_run_at) : 'never ran'} · refreshed {ago(data.generated_at)}
          {loading ? ' · updating…' : ''}
        </span>
      </div>
      <div className="grid">
        <Card title="Alerts" className="span-12">
          {data.alerts.length === 0 ? (
            <p className="muted" style={{ margin: 0 }}>
              All quiet. No alert rules are firing.
            </p>
          ) : (
            <ul className="alerts">
              {data.alerts.map((a) => (
                <li key={`${a.app}-${a.code}`}>
                  <LevelBadge level={a.level} />
                  <div>
                    <strong>{a.title}</strong> <span className="muted small">· {a.app}</span>
                    <div className="small muted">{a.detail}</div>
                  </div>
                </li>
              ))}
            </ul>
          )}
        </Card>

        <Card
          title="Harahachi"
          className="span-5"
          actions={
            <div className="row">
              <StateBadge state={hh.state} />
              <button className="link" onClick={() => navigate('/harahachi')}>
                Open
              </button>
            </div>
          }
        >
          {hh.source === 'snapshot' ? <p className="small muted">Live data unavailable — numbers from snapshot {ago(hh.as_of)}.</p> : null}
          {hh.source === 'none' ? <p className="small muted">No data yet: Harahachi has not answered and nothing has been collected.</p> : null}
          <div className="kpis">
            <Kpi label="Users" value={n(k.users)} sub={k.new_users != null ? `+${n(k.new_users)} today` : undefined} />
            <Kpi label="Active today" value={n(k.dau)} />
            <Kpi label="Meals today" value={n(k.meals_today)} />
            <Kpi label="AI calls today" value={n(k.ai_calls)} sub={`${n(k.ai_errors)} errors · ${ratio(k.ai_errors, k.ai_calls)}`} bad={!!k.ai_calls && (k.ai_errors ?? 0) / k.ai_calls > 0.1} />
            <Kpi label="Tokens (24 h)" value={compact((k.tokens_in_24h ?? 0) + (k.tokens_out_24h ?? 0))} sub={`${compact(k.tokens_in_24h)} in · ${compact(k.tokens_out_24h)} out`} />
            <Kpi label="Cap hits" value={n(k.cap_hits)} bad={(k.cap_hits ?? 0) > 0} />
            <Kpi label="5xx today" value={n(k.errors_5xx)} sub={`of ${n(k.requests)} requests`} />
          </div>
          <h3 style={{ marginTop: 14 }} className="small muted">
            Health checks, last 24 h
          </h3>
          <StatusStrip checks={hh.strip} label="Harahachi health checks" />
          <Uptime u24={hh.uptime_24h} u30={hh.uptime_30d} />
        </Card>

        <Card
          title="Bus Board"
          className="span-4"
          actions={
            <div className="row">
              <StateBadge state={bb.state} />
              <button className="link" onClick={() => navigate('/busboard')}>
                Open
              </button>
            </div>
          }
        >
          {bb.state === 'asleep' ? <p className="small muted">Asleep is normal on Render's free tier. Open the Bus Board page to wake it.</p> : null}
          <div className="kpis">
            <Kpi label="Requests today" value={n(b.requests)} sub={`${n(b.errors_5xx)} × 5xx`} />
            <Kpi label="Live streams" value={n(b.sse_open)} sub="SSE open now" />
            <Kpi label="Active vehicles" value={n(b.vehicles_active)} />
            <Kpi label="Pings today" value={n(b.pings_received)} sub={`${n(b.pings_accepted)} accepted`} />
          </div>
          <h3 style={{ marginTop: 14 }} className="small muted">
            Checks, last 24 h
          </h3>
          <StatusStrip checks={bb.strip} label="Bus Board checks" />
          <Uptime u24={bb.uptime_24h} u30={bb.uptime_30d} availability />
        </Card>

        <Card
          title="Landing"
          className="span-3"
          actions={
            <div className="row">
              <StateBadge state={landing.state} />
              <button className="link" onClick={() => navigate('/landing')}>
                Open
              </button>
            </div>
          }
        >
          <div className="kpis">
            <Kpi label="Visits today" value={n(landing.kpis.views)} />
            <Kpi label="Visitors" value={n(landing.kpis.visitors)} sub="per-day hashed" />
            <Kpi label="CTA clicks" value={n(landing.kpis.clicks)} />
            <Kpi label="Bot hits" value={n(landing.kpis.bots)} />
          </div>
          <h3 style={{ marginTop: 14 }} className="small muted">
            Checks, last 24 h
          </h3>
          <StatusStrip checks={landing.strip} label="Landing checks" />
          <Uptime u24={landing.uptime_24h} u30={landing.uptime_30d} />
        </Card>
      </div>
    </>
  );
}
