import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

// The dev server proxies /api to the local FastAPI (backend/dev_server.py).
// Production is served by Vercel: static dist/ + api/index.py.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8787', changeOrigin: false },
    },
  },
  build: {
    target: 'es2022',
    sourcemap: false,
    // No inline <script>/modulepreload polyfill: the CSP is script-src 'self'.
    modulePreload: { polyfill: false },
    assetsInlineLimit: 0,
  },
});
