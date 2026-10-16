// Hand-built SVG charts. No library, no CDN, nothing inline-scripted.
import { useId } from 'react';
import { compact } from '../fmt';

export interface Series {
  name: string;
  values: number[];
  color?: string; // CSS colour or var()
  dashed?: boolean;
}

const PALETTE = ['var(--s1)', 'var(--s2)', 'var(--s3)', 'var(--s4)', 'var(--s5)', 'var(--s6)'];

function niceMax(v: number): number {
  if (v <= 0) return 1;
  const p = 10 ** Math.floor(Math.log10(v));
  const m = v / p;
  return (m <= 1 ? 1 : m <= 2 ? 2 : m <= 5 ? 5 : 10) * p;
}

/** Multi-series line chart with a y-axis, a few x labels and a legend. */
export function LineChart({
  series,
  labels,
  height = 160,
  title,
  markers,
  area = false,
}: {
  series: Series[];
  labels: string[];
  height?: number;
  title: string;
  markers?: boolean[];
  area?: boolean;
}) {
  const id = useId();
  const W = 640;
  const H = height;
  const padL = 40;
  const padR = 8;
  const padT = 8;
  const padB = 20;
  const n = Math.max(1, ...series.map((s) => s.values.length));
  const max = niceMax(Math.max(0, ...series.flatMap((s) => s.values.filter((v) => Number.isFinite(v)))));
  const x = (i: number) => padL + (n <= 1 ? 0 : (i * (W - padL - padR)) / (n - 1));
  const y = (v: number) => padT + (H - padT - padB) * (1 - v / max);
  const ticks = [0, max / 2, max];
  const labelIdx = n <= 1 ? [0] : [0, Math.floor((n - 1) / 2), n - 1];
  if (!series.length || n === 0 || series.every((s) => s.values.length === 0)) {
    return <p className="muted small">No data yet.</p>;
  }
  return (
    <figure style={{ margin: 0 }}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${id}-t`}>
        <title id={`${id}-t`}>{title}</title>
        {ticks.map((t) => (
          <g key={t}>
            <line className="grid-line" x1={padL} x2={W - padR} y1={y(t)} y2={y(t)} />
            <text className="axis" x={padL - 6} y={y(t) + 3} textAnchor="end">
              {compact(t)}
            </text>
          </g>
        ))}
        {markers?.map((m, i) =>
          m ? <line key={`m${i}`} x1={x(i)} x2={x(i)} y1={padT} y2={H - padB} stroke="var(--crit)" strokeDasharray="2 3" strokeWidth={1} /> : null,
        )}
        {series.map((s, si) => {
          const pts = s.values.map((v, i) => `${x(i).toFixed(1)},${y(Number.isFinite(v) ? v : 0).toFixed(1)}`);
          const color = s.color ?? PALETTE[si % PALETTE.length];
          return (
            <g key={s.name}>
              {area && si === 0 && pts.length > 1 ? (
                <polygon
                  points={`${x(0)},${y(0)} ${pts.join(' ')} ${x(s.values.length - 1)},${y(0)}`}
                  fill={color}
                  opacity={0.08}
                />
              ) : null}
              <polyline
                points={pts.join(' ')}
                fill="none"
                stroke={color}
                strokeWidth={1.6}
                strokeDasharray={s.dashed ? '4 3' : undefined}
                strokeLinejoin="round"
                vectorEffect="non-scaling-stroke"
              />
            </g>
          );
        })}
        {labelIdx.map((i, k) => (
          <text key={k} className="axis" x={x(i)} y={H - 4} textAnchor={k === 0 ? 'start' : k === 2 ? 'end' : 'middle'}>
            {labels[i] ?? ''}
          </text>
        ))}
      </svg>
      {series.length > 1 || series[0]?.name ? (
        <figcaption className="legend">
          {series.map((s, si) => (
            <span key={s.name}>
              <i style={{ background: s.color ?? PALETTE[si % PALETTE.length] }} />
              {s.name}
            </span>
          ))}
        </figcaption>
      ) : null}
    </figure>
  );
}

/** Stacked daily bars (e.g. meals by source). */
export function StackedBars({ series, labels, title, height = 160 }: { series: Series[]; labels: string[]; title: string; height?: number }) {
  const id = useId();
  const W = 640;
  const H = height;
  const padL = 40;
  const padB = 20;
  const padT = 8;
  const n = Math.max(0, ...series.map((s) => s.values.length));
  if (n === 0) return <p className="muted small">No data yet.</p>;
  const totals = Array.from({ length: n }, (_, i) => series.reduce((a, s) => a + (s.values[i] ?? 0), 0));
  const max = niceMax(Math.max(0, ...totals));
  const bw = (W - padL - 4) / n;
  const y = (v: number) => ((H - padT - padB) * v) / max;
  return (
    <figure style={{ margin: 0 }}>
      <svg className="chart" viewBox={`0 0 ${W} ${H}`} role="img" aria-labelledby={`${id}-t`}>
        <title id={`${id}-t`}>{title}</title>
        {[0, max / 2, max].map((t) => (
          <g key={t}>
            <line className="grid-line" x1={padL} x2={W} y1={H - padB - y(t)} y2={H - padB - y(t)} />
            <text className="axis" x={padL - 6} y={H - padB - y(t) + 3} textAnchor="end">
              {compact(t)}
            </text>
          </g>
        ))}
        {Array.from({ length: n }, (_, i) => {
          let acc = 0;
          return (
            <g key={i}>
              {series.map((s, si) => {
                const v = s.values[i] ?? 0;
                const h = y(v);
                acc += h;
                return (
                  <rect
                    key={s.name}
                    x={padL + i * bw + 1}
                    width={Math.max(1, bw - 2)}
                    y={H - padB - acc}
                    height={h}
                    fill={s.color ?? PALETTE[si % PALETTE.length]}
                    opacity={0.85}
                  >
                    <title>{`${labels[i] ?? ''} · ${s.name}: ${v}`}</title>
                  </rect>
                );
              })}
            </g>
          );
        })}
        <text className="axis" x={padL} y={H - 4}>
          {labels[0] ?? ''}
        </text>
        <text className="axis" x={W} y={H - 4} textAnchor="end">
          {labels[n - 1] ?? ''}
        </text>
      </svg>
      <figcaption className="legend">
        {series.map((s, si) => (
          <span key={s.name}>
            <i style={{ background: s.color ?? PALETTE[si % PALETTE.length] }} />
            {s.name}
          </span>
        ))}
      </figcaption>
    </figure>
  );
}

/** Horizontal bars for ranked lists (top pages, referrers, demand...). */
export function HBars({ rows, unit = '', empty = 'Nothing yet.' }: { rows: Array<[string, number]>; unit?: string; empty?: string }) {
  if (!rows.length) return <p className="muted small">{empty}</p>;
  const max = Math.max(1, ...rows.map((r) => r[1]));
  return (
    <div role="list">
      {rows.map(([label, v]) => (
        <div className="hbar" role="listitem" key={label}>
          <span className="hbar-label" title={label}>
            {label}
          </span>
          <span className="hbar-track" aria-hidden="true">
            <span className="hbar-fill" style={{ width: `${(100 * v) / max}%`, display: 'block' }} />
          </span>
          <span className="num" style={{ textAlign: 'right' }}>
            {compact(v)}
            {unit}
          </span>
        </div>
      ))}
    </div>
  );
}

/** One cell per uptime check, coloured by state. SVG so hundreds of checks
 * always fit the width (no per-cell minimum, no overflow on phones). */
export function StatusStrip({ checks, label }: { checks: Array<{ at: string; state: string }>; label: string }) {
  const up = checks.filter((c) => c.state === 'up').length;
  const n = Math.max(1, checks.length);
  return (
    <svg className="strip" viewBox={`0 0 ${n} 1`} preserveAspectRatio="none" role="img" aria-label={`${label}: ${up} of ${checks.length} checks up`}>
      {checks.length === 0 ? <rect className="none" x={0} y={0} width={1} height={1} /> : null}
      {checks.map((c, i) => (
        <rect key={c.at} className={c.state} x={i + 0.08} y={0} width={0.84} height={1}>
          <title>{`${new Date(c.at).toLocaleString()} · ${c.state}`}</title>
        </rect>
      ))}
    </svg>
  );
}

export function Sparkline({ values, title }: { values: number[]; title: string }) {
  const W = 120;
  const H = 28;
  if (values.length < 2) return null;
  const max = Math.max(1, ...values);
  const pts = values.map((v, i) => `${((i * W) / (values.length - 1)).toFixed(1)},${(H - 2 - ((H - 4) * v) / max).toFixed(1)}`);
  return (
    <svg viewBox={`0 0 ${W} ${H}`} width={W} height={H} role="img" aria-label={title}>
      <polyline points={pts.join(' ')} fill="none" stroke="var(--ink-2)" strokeWidth={1.4} />
    </svg>
  );
}
