import { useState } from 'react';
import { HBars, LineChart } from '../components/Charts';
import { Card, ErrorNote, Kpi, Loading, useApi } from '../components/ui';
import { n, pct } from '../fmt';
import type { Landing as LandingData } from '../types';

const CTA_LABEL: Record<string, string> = {
  'harahachi:open': 'Harahachi · open app',
  'harahachi:apk': 'Harahachi · download APK',
  'busboard:open': 'Bus Board · open app',
  'busboard:apk': 'Bus Board · download APK',
};

export function Landing() {
  const [days, setDays] = useState(30);
  const { data, error, reload } = useApi<LandingData>(`/api/landing?days=${days}`, 120_000);
  if (error && !data) return <ErrorNote error={error} onRetry={reload} />;
  return (
    <>
      <div className="page-head">
        <h1>Landing</h1>
        <label>
          Window
          <select value={days} onChange={(e) => setDays(Number(e.target.value))}>
            <option value={7}>7 days</option>
            <option value={30}>30 days</option>
            <option value={90}>90 days</option>
          </select>
        </label>
      </div>
      {!data ? (
        <Loading />
      ) : (
        <div className="grid">
          <Card title="Today" className="span-4">
            <div className="kpis">
              <Kpi label="Page views" value={n(data.today.pageviews)} />
              <Kpi label="Visitors" value={n(data.today.visitors)} sub="unique-ish, per day" />
              <Kpi label="CTA clicks" value={n(data.today.clicks)} />
            </div>
            <div className="kpis" style={{ marginTop: 12 }}>
              <Kpi label={`Views, ${days} d`} value={n(data.totals.pageviews)} />
              <Kpi label={`Clicks, ${days} d`} value={n(data.totals.clicks)} />
              <Kpi label="Bot share" value={pct(100 * data.totals.bot_share)} sub={`${n(data.totals.bots)} hits excluded`} />
            </div>
          </Card>
          <Card title="Visits per day" className="span-8">
            <LineChart
              title="Landing page views, visitors and CTA clicks per day"
              labels={data.days.map((d) => d.slice(5))}
              area
              series={[
                { name: 'Page views', values: data.series.pageviews },
                { name: 'Visitors', values: data.series.visitors, color: 'var(--s3)' },
                { name: 'CTA clicks', values: data.series.clicks, color: 'var(--s2)' },
              ]}
            />
            <p className="small muted">Visitors are counted with a hash salted per day and never linked across days; DNT/GPC browsers send nothing.</p>
          </Card>
          <Card title="CTA clicks" className="span-6">
            <HBars rows={data.ctas.map(([k, v]) => [CTA_LABEL[k] ?? k, v] as [string, number])} empty="No clicks yet." />
          </Card>
          <Card title="Top pages" className="span-6">
            <HBars rows={data.top_pages} />
          </Card>
          <Card title="Referrers" className="span-4">
            <HBars rows={data.referrers} />
          </Card>
          <Card title="Countries" className="span-4">
            <HBars rows={data.countries.map(([c, v]) => [c === '??' ? 'Unknown' : c, v] as [string, number])} />
          </Card>
          <Card title="Devices" className="span-4">
            <HBars rows={data.devices} />
          </Card>
        </div>
      )}
    </>
  );
}
