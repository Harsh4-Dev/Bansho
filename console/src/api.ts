// Same-origin JSON client. Adds the per-session CSRF header to every non-GET,
// and turns a "step_up_required" refusal into a passkey prompt + one retry.
import { startAuthentication, startRegistration } from '@simplewebauthn/browser';
import type {
  PublicKeyCredentialCreationOptionsJSON,
  PublicKeyCredentialRequestOptionsJSON,
} from '@simplewebauthn/browser';

export interface SessionInfo {
  kind: 'full' | 'recovery';
  csrf: string;
  expires_at: string;
  idle_expires_at: string;
  step_up_until: string | null;
}

export class ApiError extends Error {
  constructor(
    public status: number,
    public code: string,
    message: string,
  ) {
    super(message);
  }
}

let csrf: string | null = null;
let onUnauthenticated: (() => void) | null = null;

export function setSession(s: SessionInfo | null): void {
  csrf = s ? s.csrf : null;
}

export function onSignedOut(fn: () => void): void {
  onUnauthenticated = fn;
}

async function request<T>(method: string, path: string, body?: unknown, retry = true, background = false): Promise<T> {
  const headers: Record<string, string> = { accept: 'application/json' };
  // Auto-refresh does not count as activity for the 1 h idle timeout.
  if (background) headers['x-bansho-background'] = '1';
  if (method !== 'GET') {
    headers['content-type'] = 'application/json';
    // Before sign-in there is no session token; the header's presence is
    // what proves same-origin for the anonymous auth endpoints.
    headers['x-bansho-csrf'] = csrf ?? 'anon';
  }
  const res = await fetch(path, {
    method,
    headers,
    body: method === 'GET' ? undefined : JSON.stringify(body ?? {}),
    credentials: 'same-origin',
    cache: 'no-store',
    redirect: 'error',
  });
  let data: unknown = null;
  const text = await res.text();
  if (text) {
    try {
      data = JSON.parse(text);
    } catch {
      data = null;
    }
  }
  if (!res.ok) {
    const err = (data as { error?: { code?: string; message?: string } } | null)?.error;
    const code = err?.code ?? `http_${res.status}`;
    if (code === 'step_up_required' && retry) {
      await stepUp();
      return request<T>(method, path, body, false);
    }
    if (res.status === 401 && code === 'unauthenticated' && onUnauthenticated) onUnauthenticated();
    throw new ApiError(res.status, code, err?.message ?? `Request failed (${res.status})`);
  }
  return data as T;
}

export const api = {
  get: <T>(path: string, background = false) => request<T>('GET', path, undefined, true, background),
  post: <T>(path: string, body?: unknown) => request<T>('POST', path, body),
};

interface Ceremony<O> {
  ceremony_id: string;
  options: O;
}

/** Step-up: a fresh passkey assertion bound to the current session. */
export async function stepUp(): Promise<void> {
  const o = await request<Ceremony<PublicKeyCredentialRequestOptionsJSON>>('POST', '/api/auth/reauth/options', {}, false);
  const credential = await startAuthentication({ optionsJSON: o.options });
  const r = await request<{ session: SessionInfo }>('POST', '/api/auth/reauth/verify', {
    ceremony_id: o.ceremony_id,
    credential,
  }, false);
  setSession(r.session);
}

export async function passkeyLogin(): Promise<SessionInfo> {
  const o = await api.post<Ceremony<PublicKeyCredentialRequestOptionsJSON>>('/api/auth/login/options');
  const credential = await startAuthentication({ optionsJSON: o.options });
  const r = await api.post<{ session: SessionInfo }>('/api/auth/login/verify', { ceremony_id: o.ceremony_id, credential });
  setSession(r.session);
  return r.session;
}

export async function passkeySetup(setupToken: string, name: string): Promise<{ session: SessionInfo; recovery_codes: string[] }> {
  const o = await api.post<Ceremony<PublicKeyCredentialCreationOptionsJSON>>('/api/auth/setup/options', { setup_token: setupToken });
  const credential = await startRegistration({ optionsJSON: o.options });
  const r = await api.post<{ session: SessionInfo; recovery_codes: string[] }>('/api/auth/setup/verify', {
    setup_token: setupToken,
    ceremony_id: o.ceremony_id,
    credential,
    name,
  });
  setSession(r.session);
  return r;
}

/** Register another passkey (full session after step-up, or a recovery session). */
export async function passkeyAdd(name: string): Promise<{ next?: string }> {
  const o = await api.post<Ceremony<PublicKeyCredentialCreationOptionsJSON>>('/api/security/passkeys/options');
  const credential = await startRegistration({ optionsJSON: o.options });
  return api.post<{ next?: string }>('/api/security/passkeys/verify', { ceremony_id: o.ceremony_id, credential, name });
}

export function describeError(e: unknown): string {
  if (e instanceof ApiError) return e.message;
  if (e instanceof Error) {
    if (e.name === 'NotAllowedError') return 'The passkey prompt was dismissed or timed out.';
    return e.message;
  }
  return 'Something went wrong.';
}
