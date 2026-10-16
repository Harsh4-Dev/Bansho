import type { UpstreamInfo } from './components/ui';

export interface Proxied<T> {
  upstream: UpstreamInfo;
  data: T | null;
  message: string | null;
}

export interface UptimeStats {
  checks: number;
  up: number;
  asleep: number;
  down: number;
  pct_up: number | null;
  pct_available: number | null;
}

export interface Check {
  at: string;
  state: string;
  latency_ms?: number;
}

export interface Alert {
  level: 'crit' | 'warn' | 'info';
  app: string;
  code: string;
  title: string;
  detail: string;
}

type Num = number | null | undefined;

export interface Overview {
  generated_at: string;
  apps: {
    harahachi: {
      state: string;
      upstream: UpstreamInfo;
      source: string;
      as_of: string | null;
      uptime_24h: UptimeStats;
      uptime_30d: UptimeStats;
      strip: Check[];
      kpis: Record<string, Num>;
    };
    busboard: {
      state: string;
      upstream: UpstreamInfo;
      source: string;
      as_of: string | null;
      uptime_24h: UptimeStats;
      uptime_30d: UptimeStats;
      strip: Check[];
      kpis: Record<string, Num>;
    };
    landing: { state: string; uptime_24h: UptimeStats; uptime_30d: UptimeStats; strip: Check[]; kpis: Record<string, number> };
  };
  alerts: Alert[];
  collector: { last_run_at: string | null; last_result: Record<string, unknown> | null };
}

// ---- Harahachi ops API (contract in SPEC.md) --------------------------------

export interface SlotStats {
  calls?: number;
  errors?: number;
  p50_ms?: number;
  p95_ms?: number;
  tokens_in?: number;
  tokens_out?: number;
  model?: string;
  fallback?: string;
}

export interface HhSummary {
  generated_at?: string;
  version?: string;
  totals?: Record<string, Num>;
  today?: Record<string, Num>;
  ai_by_slot?: Record<string, SlotStats>;
  clients?: Record<string, number | { count?: number; versions?: Record<string, number> }>;
  client_versions?: Record<string, Record<string, number>>;
  health?: Record<string, boolean>;
  top_dishes?: Array<{ name: string; count: number }>;
}

export interface HhTimeseries {
  days: string[];
  series: Record<string, number[]>;
}

export interface HhRetention {
  weeks?: number;
  cohorts?: Array<{ week: string; size: number; active_pct: number[] }>;
}

export interface HhFunnel {
  paired_devices?: number;
  registered_users?: number;
  first_meal?: number;
  active_3_days?: number;
  active_7_days?: number;
}

export interface HhUserRow {
  id: number | string;
  username: string;
  created_at?: string;
  last_active?: string;
  meals?: number;
  streak_current?: number;
  client_kind?: string;
  is_admin?: boolean;
  locked?: boolean;
  ai_calls_7d?: number;
}

export interface HhDevice {
  id: string | number;
  client_kind?: string;
  client_version?: string;
  last_seen?: string;
  revoked?: boolean;
}

export interface HhUserDetail {
  user?: Record<string, unknown> & { id?: number | string; username?: string; is_admin?: boolean; locked?: boolean };
  profile?: Record<string, unknown>;
  targets?: Record<string, unknown>;
  body_fat?: Record<string, unknown>;
  meals?: Array<Record<string, unknown>>;
  weights?: Array<Record<string, unknown>>;
  workouts?: Array<Record<string, unknown>>;
  devices?: HhDevice[];
  ai_usage?: Record<string, SlotStats>;
  [k: string]: unknown;
}

// ---- Bus Board ----------------------------------------------------------------

export interface BbRoute {
  count: number;
  errors_4xx: number;
  errors_5xx: number;
  p50_ms: number;
  p95_ms: number;
}

export interface BbStats {
  version?: string;
  boot_id?: string;
  started_at?: string | number;
  uptime_s?: number;
  requests?: { by_route?: Record<string, BbRoute> };
  sse?: { open?: number; total_opened?: number };
  board_queries?: { top?: Array<{ from: string; to: string; count: number }> };
  pings?: { received?: number; accepted?: number; rejected?: Record<string, number> };
  contributors?: {
    count?: number;
    trust_histogram?: Record<string, number>;
    top?: Array<{ id_hash: string; score: number; accepted: number; blocked?: boolean }>;
  };
  vehicles?: { active?: number; by_route?: Record<string, number> };
  feed?: { stops?: number; unverified_stops?: number; routes?: number; version?: string };
  runtime?: { goroutines?: number; heap_mb?: number; go_version?: string };
  blocked?: string[];
}

export interface BbLive {
  state: string;
  upstream: UpstreamInfo;
  stats: BbStats | null;
  source: 'live' | 'snapshot' | 'none';
  as_of: string | null;
  uptime_24h: UptimeStats;
  uptime_30d: UptimeStats;
  strip: Check[];
  timeline: {
    boots: Array<{ boot_id: string; started_at: string; last_seen_at: string; uptime_s: number }>;
    events: Array<{ at: string; kind: string; gap_s?: number; boot_id?: string; version?: string }>;
  };
  today: Record<string, number>;
}

export interface BbTraffic {
  hours: number;
  series: Record<string, Array<number | boolean | string>> & { at: string[]; restart: boolean[] };
  routes: Record<string, { count: number[]; errors_5xx: number[]; p95_ms: number[]; total: number }>;
}

export interface Landing {
  days: string[];
  series: { pageviews: number[]; visitors: number[]; clicks: number[] };
  totals: { pageviews: number; clicks: number; bots: number; bot_share: number };
  today: { pageviews: number; visitors: number; clicks: number };
  top_pages: Array<[string, number]>;
  referrers: Array<[string, number]>;
  countries: Array<[string, number]>;
  devices: Array<[string, number]>;
  ctas: Array<[string, number]>;
}

export interface AuditRow {
  id: number;
  at: string;
  actor: string;
  action: string;
  target: string;
  outcome: string;
  country: string;
  client: string;
  ip_hash: string;
  detail: Record<string, unknown>;
}
