/// <reference types="vite/client" />

interface ImportMetaEnv {
  /** Backend base URL, e.g. http://localhost:8000 */
  readonly VITE_API_BASE_URL?: string;
  /** AMap JS API 2.0 key (Web 端) */
  readonly VITE_AMAP_JS_KEY?: string;
  /** AMap JS API 2.0 security code (安全密钥 jscode) */
  readonly VITE_AMAP_JS_SECURITY_CODE?: string;
}

interface ImportMeta {
  readonly env: ImportMetaEnv;
}

// AMap is loaded at runtime via the JS API loader; keep it loosely typed.
interface Window {
  _AMapSecurityConfig?: { securityJsCode: string };
  AMap?: any;
}
