const nf = new Intl.NumberFormat('en-IN');
const nf1 = new Intl.NumberFormat('en-IN', { maximumFractionDigits: 1 });

export function n(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return nf.format(Math.round(v));
}

export function n1(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return nf1.format(v);
}

export function compact(v: number | null | undefined): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  const a = Math.abs(v);
  if (a >= 1e9) return `${nf1.format(v / 1e9)}B`;
  if (a >= 1e6) return `${nf1.format(v / 1e6)}M`;
  if (a >= 1e4) return `${nf1.format(v / 1e3)}k`;
  return nf.format(Math.round(v));
}

export function pct(v: number | null | undefined, digits = 1): string {
  if (v === null || v === undefined || Number.isNaN(v)) return '—';
  return `${v.toFixed(digits)} %`;
}

export function ratio(a: number | null | undefined, b: number | null | undefined): string {
  if (!a || !b) return a === 0 && b ? '0 %' : '—';
  return pct((100 * a) / b);
}

export function when(iso: string | null | undefined): string {
  if (!iso) return '—';
  const d = new Date(iso);
  if (Number.isNaN(d.getTime())) return iso;
  return d.toLocaleString(undefined, { month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit' });
}

export function ago(iso: string | null | undefined): string {
  if (!iso) return '—';
  const s = Math.round((Date.now() - new Date(iso).getTime()) / 1000);
  if (Number.isNaN(s)) return '—';
  if (s < 0) return 'just now';
  if (s < 60) return `${s}s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} d ago`;
}

export function duration(sec: number | null | undefined): string {
  if (sec === null || sec === undefined) return '—';
  if (sec < 60) return `${Math.round(sec)}s`;
  if (sec < 3600) return `${Math.round(sec / 60)} min`;
  if (sec < 86400) return `${(sec / 3600).toFixed(1)} h`;
  return `${(sec / 86400).toFixed(1)} d`;
}

export function sum(xs: Array<number | null | undefined> | undefined): number {
  return (xs ?? []).reduce<number>((a, b) => a + (b ?? 0), 0);
}
