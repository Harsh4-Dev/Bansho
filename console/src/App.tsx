import { useCallback, useEffect, useState } from 'react';
import type { MouseEvent } from 'react';
import { api, onSignedOut, setSession } from './api';
import type { SessionInfo } from './api';
import { FeedbackProvider, Loading } from './components/ui';
import { navigate, usePath } from './router';
import { Audit } from './screens/Audit';
import { Login, Recover, RecoveryRegister, Setup } from './screens/Auth';
import { BusBoard } from './screens/BusBoard';
import { Harahachi } from './screens/Harahachi';
import { Landing } from './screens/Landing';
import { Overview } from './screens/Overview';
import { Security } from './screens/Security';

const NAV: Array<[string, string]> = [
  ['/', 'Overview'],
  ['/harahachi', 'Harahachi'],
  ['/busboard', 'Bus Board'],
  ['/landing', 'Landing'],
  ['/audit', 'Audit'],
  ['/security', 'Security'],
];

type Theme = 'auto' | 'light' | 'dark';

function readTheme(): Theme {
  try {
    const t = window.localStorage.getItem('bansho.theme');
    return t === 'light' || t === 'dark' ? t : 'auto';
  } catch {
    return 'auto';
  }
}

function applyTheme(t: Theme): void {
  if (t === 'auto') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', t);
  try {
    window.localStorage.setItem('bansho.theme', t);
  } catch {
    /* storage unavailable: theme still applies for this visit */
  }
}

type State = { status: 'loading' } | { status: 'anon' } | { status: 'in'; session: SessionInfo };

export function App() {
  const path = usePath();
  const [state, setState] = useState<State>({ status: 'loading' });
  const [theme, setTheme] = useState<Theme>(readTheme);

  useEffect(() => applyTheme(theme), [theme]);

  const refresh = useCallback(async () => {
    try {
      const r = await api.get<{ authenticated: boolean; session?: SessionInfo }>('/api/session');
      if (r.authenticated && r.session) {
        setSession(r.session);
        setState({ status: 'in', session: r.session });
      } else {
        setSession(null);
        setState({ status: 'anon' });
      }
    } catch {
      setState({ status: 'anon' });
    }
  }, []);

  useEffect(() => {
    void refresh();
    onSignedOut(() => {
      setSession(null);
      setState({ status: 'anon' });
    });
  }, [refresh]);

  const signedIn = (s: SessionInfo) => {
    setSession(s);
    setState({ status: 'in', session: s });
    if (['/login', '/setup', '/recover'].includes(window.location.pathname)) navigate('/');
  };

  if (state.status === 'loading') {
    return (
      <main className="auth">
        <Loading />
      </main>
    );
  }

  if (state.status === 'anon') {
    return (
      <FeedbackProvider>
        {path === '/setup' ? <Setup onSignedIn={signedIn} /> : path === '/recover' ? <Recover onSignedIn={signedIn} /> : <Login onSignedIn={signedIn} />}
      </FeedbackProvider>
    );
  }

  if (state.session.kind === 'recovery') {
    return (
      <FeedbackProvider>
        <RecoveryRegister
          onDone={() => {
            setState({ status: 'anon' });
            navigate('/login');
          }}
        />
      </FeedbackProvider>
    );
  }

  const go = (e: MouseEvent<HTMLAnchorElement>, to: string) => {
    if (e.metaKey || e.ctrlKey || e.shiftKey || e.button !== 0) return;
    e.preventDefault();
    navigate(to);
  };

  const logout = async () => {
    try {
      await api.post('/api/auth/logout');
    } finally {
      setSession(null);
      setState({ status: 'anon' });
      navigate('/login');
    }
  };

  const page =
    path === '/harahachi' ? <Harahachi /> :
    path === '/busboard' ? <BusBoard /> :
    path === '/landing' ? <Landing /> :
    path === '/audit' ? <Audit /> :
    path === '/security' ? <Security /> :
    <Overview />;

  return (
    <FeedbackProvider>
      <a className="skip" href="#main">
        Skip to content
      </a>
      <header className="top">
        <div className="top-inner">
          <a className="brand" href="/" onClick={(e) => go(e, '/')}>
            <span className="brand-seal" aria-hidden="true">
              番
            </span>
            Bansho
          </a>
          <nav className="nav" aria-label="Main">
            {NAV.map(([to, label]) => (
              <a key={to} href={to} aria-current={(to === '/' ? path === '/' || !NAV.some(([p]) => p === path) : path === to) ? 'page' : undefined} onClick={(e) => go(e, to)}>
                {label}
              </a>
            ))}
          </nav>
          <div className="top-tools">
            <label className="sr-only" htmlFor="theme">
              Theme
            </label>
            <select id="theme" value={theme} onChange={(e) => setTheme(e.target.value as Theme)} title="Theme">
              <option value="auto">Auto</option>
              <option value="light">Light</option>
              <option value="dark">Dark</option>
            </select>
            <button onClick={logout} title={`Session ends by ${new Date(state.session.expires_at).toLocaleTimeString()}`}>
              Sign out
            </button>
          </div>
        </div>
      </header>
      <main className="main" id="main" tabIndex={-1}>
        {page}
      </main>
    </FeedbackProvider>
  );
}
