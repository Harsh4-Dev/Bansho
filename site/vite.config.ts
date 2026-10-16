import { resolve } from 'node:path';
import { defineConfig, loadEnv } from 'vite';
import type { Plugin } from 'vite';

/**
 * Build-time wiring for the static landing site.
 *
 * - Call-to-action links are written in the HTML as
 *     <a data-cta="VITE_HARAHACHI_APK_URL" data-track="harahachi:apk" href="#">…</a>
 *   and this plugin either fills in the real URL or REMOVES the whole link when
 *   the variable is missing or not an http(s) URL. No broken buttons.
 * - A <meta> CSP narrows connect-src to exactly the console's ingest origin
 *   (Vercel's header CSP allows https:, the browser enforces both).
 * - %INGEST_URL% is exposed to the beacon script via import.meta.env.
 */

function validUrl(raw: string | undefined): string | null {
  if (!raw) return null;
  try {
    const u = new URL(raw.trim());
    if (u.protocol === 'https:') return u.toString();
    if (u.protocol === 'http:' && (u.hostname === 'localhost' || u.hostname === '127.0.0.1')) return u.toString();
  } catch {
    /* fall through */
  }
  return null;
}

function escapeAttr(s: string): string {
  return s.replace(/&/g, '&amp;').replace(/"/g, '&quot;').replace(/</g, '&lt;');
}

function landingHtml(env: Record<string, string>): Plugin {
  const ingest = validUrl(env.VITE_INGEST_URL);
  const connect = ingest ? new URL(ingest).origin : "'none'";
  return {
    name: 'bansho-landing-html',
    // Dev only: serve /harahachi like Vercel does (directory index, no slash).
    configureServer(server) {
      server.middlewares.use((req, _res, next) => {
        if (req.url && /^\/(harahachi|busboard|privacy)(\?.*)?$/.test(req.url)) req.url = req.url.replace(/^(\/[a-z]+)/, '$1/');
        next();
      });
    },
    transformIndexHtml(html, ctx) {
      const withCtas = html.replace(
        /<a\b([^>]*?)\sdata-cta="(VITE_[A-Z_]+)"([^>]*)>([\s\S]*?)<\/a>/g,
        (_m, before: string, name: string, after: string, inner: string) => {
          const url = validUrl(env[name]);
          if (!url) return `<!-- ${name} not set: link hidden -->`;
          const attrs = `${before}${after}`.replace(/\shref="[^"]*"/, '');
          return `<a${attrs} href="${escapeAttr(url)}" rel="noopener">${inner}</a>`;
        },
      );
      // Dev server: no meta CSP (it would block Vite's HMR websocket).
      if (ctx.server) return withCtas;
      return withCtas.replace(
        '<meta charset="UTF-8" />',
        `<meta charset="UTF-8" />\n    <meta http-equiv="Content-Security-Policy" content="connect-src ${connect}" />`,
      );
    },
  };
}

export default defineConfig(({ mode }) => {
  const env = loadEnv(mode, process.cwd(), 'VITE_');
  return {
    appType: 'mpa',
    plugins: [landingHtml(env)],
    server: { port: 5174, strictPort: true },
    build: {
      target: 'es2022',
      sourcemap: false,
      assetsInlineLimit: 0, // fonts as files: CSP font-src 'self' has no data:
      modulePreload: { polyfill: false },
      rollupOptions: {
        input: {
          home: resolve(__dirname, 'index.html'),
          harahachi: resolve(__dirname, 'harahachi/index.html'),
          busboard: resolve(__dirname, 'busboard/index.html'),
          privacy: resolve(__dirname, 'privacy/index.html'),
        },
      },
    },
  };
});
