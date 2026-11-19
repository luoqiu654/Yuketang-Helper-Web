/// <reference types="vite/client" />

interface ImportMetaEnv {
  readonly VITE_APP_VERSION: string
  readonly VITE_DESKTOP_BUILD?: string
}

interface ImportMeta {
  readonly env: ImportMetaEnv
}
