/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_INGEST_URL?: string;
  readonly VITE_HARAHACHI_APP_URL?: string;
  readonly VITE_HARAHACHI_APK_URL?: string;
  readonly VITE_BUSBOARD_APP_URL?: string;
  readonly VITE_BUSBOARD_APK_URL?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
