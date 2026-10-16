/**
 * The landing site's only analytics: one beacon per page view and per
 * call-to-action click, carrying {type, path, ref_host, target?} and nothing
 * else. No cookies, no storage, no identifiers.
 *
 * Nothing is sent at all when the browser says Do Not Track or Global Privacy
 * Control, or when VITE_INGEST_URL was not set at build time.
 */

type Beacon = { type: 'pageview' | 'click'; path: string; ref_host: string; target?: string };

const INGEST: string = import.meta.env.VITE_INGEST_URL ?? '';

function optedOut(): boolean {
  const nav = navigator as Navigator & { globalPrivacyControl?: boolean; msDoNotTrack?: string };
  const win = window as Window & { doNotTrack?: string };
  return nav.doNotTrack === '1' || win.doNotTrack === '1' || nav.msDoNotTrack === '1' || nav.globalPrivacyControl === true;
}

function pagePath(): string {
  let p = location.pathname.replace(/index\.html$/, '').replace(/\.html$/, '');
  if (p.length > 1) p = p.replace(/\/+$/, '');
  return /^\/[A-Za-z0-9/_\-.]{0,120}$/.test(p) ? p : '/';
}

function refHost(): string {
  try {
    if (!document.referrer) return '';
    const h = new URL(document.referrer).hostname.toLowerCase();
    return h === location.hostname ? '' : h.slice(0, 100);
  } catch {
    return '';
  }
}

function send(b: Beacon): void {
  if (!INGEST || optedOut()) return;
  // A plain string body goes out as text/plain: a CORS-safelisted type, so no
  // preflight and no credentials-bearing request is needed.
  const body = JSON.stringify(b);
  try {
    if (navigator.sendBeacon && navigator.sendBeacon(INGEST, body)) return;
  } catch {
    /* fall back below */
  }
  void fetch(INGEST, { method: 'POST', body, keepalive: true, mode: 'cors', credentials: 'omit', headers: { 'content-type': 'text/plain' } }).catch(() => undefined);
}

export function startBeacon(): void {
  const path = pagePath();
  send({ type: 'pageview', path, ref_host: refHost() });
  document.addEventListener('click', (e) => {
    const el = (e.target as Element | null)?.closest?.('a[data-track]');
    const target = el?.getAttribute('data-track');
    if (target && /^[a-z0-9_:\-.]{1,64}$/.test(target)) send({ type: 'click', path, ref_host: '', target });
  });
}
