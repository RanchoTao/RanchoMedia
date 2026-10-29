export interface Snapshot {
  config: {
    account_name: string;
    app_id: string;
    app_id_sha256: string;
    has_secret: boolean;
    has_token: boolean;
    thumb_media_id: string;
    encrypted: boolean;
    credential_error: string | null;
    cover_name: string | null;
    cover_sha256: string;
    article_title: string;
    variant_sha256: string;
    sdk_ready: boolean;
  };
  receipt: {
    status: string;
    errcode: number | null;
    message: string;
    time: string | null;
    draft_media_id: string | null;
    token_state: string | null;
    cover_state: string | null;
    draft_state: string | null;
    unknown: boolean;
    permission_verified: boolean;
    token_time: string | null;
    permission_time: string | null;
    http_status: number | null;
    elapsed_ms: number | null;
    network_mode: string;
    history: { errcode: number; time: string | null; message: string }[];
  };
  run: {
    running: boolean;
    locked: boolean;
    operation: string | null;
    status: string | null;
    started_at: string | null;
    finished_at: string | null;
  };
  events: { time: string; action: string; code: string }[];
  network_disabled: boolean;
  workspace: {
    database_status: string;
    articles: { id: string; title: string; status: string }[];
    tasks: { id: string; kind: string; status: string }[];
  };
}
export interface Config {
  account_name: string;
  app_id: string;
  app_secret: string;
  access_token: string;
  thumb_media_id: string;
}
export type Operation =
  | "token-only"
  | "probe-read-only"
  | "execute"
  | "reconcile";
export type Envelope<T> =
  | { ok: true; data: T }
  | { ok: false; error: { code: string; message: string } };
export interface NativeAPI {
  get_snapshot: () => Promise<Envelope<Snapshot>>;
  save_config: (config: Config) => Promise<Envelope<Snapshot>>;
  choose_cover: () => Promise<Envelope<Snapshot>>;
  start_operation: (payload: {
    operation: Operation;
    confirmed: boolean;
    app_id_sha256: string;
    variant_sha256: string;
    cover_sha256: string;
  }) => Promise<Envelope<Snapshot>>;
  open_docs: (page: string) => Promise<Envelope<{ opened: boolean }>>;
}
declare global {
  interface Window {
    pywebview?: { api: NativeAPI };
  }
}
