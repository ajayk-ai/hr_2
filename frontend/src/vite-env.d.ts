/// <reference types="vite/client" />

/**
 * Typed build-time environment.
 *
 * Without this, `import.meta.env.VITE_*` falls through Vite's index signature and
 * resolves to `any`, so a typo in a variable name compiles cleanly and fails at
 * runtime.
 */
interface ImportMetaEnv {
  /** API origin. Empty in dev, where the Vite proxy handles it. */
  readonly VITE_API_BASE_URL?: string;
  /** Bearer token. Embedded in the built bundle -- see frontend/.env.example. */
  readonly VITE_API_TOKEN?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}
