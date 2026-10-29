import { useCallback, useEffect, useState } from "react";
import {
  Activity,
  ArrowRight,
  CalendarClock,
  Check,
  ChevronDown,
  ChevronRight,
  CircleHelp,
  FileText,
  FolderClosed,
  ImagePlus,
  LayoutDashboard,
  LockKeyhole,
  MessageSquare,
  Monitor,
  RefreshCw,
  Send,
  Settings2,
  ShieldCheck,
  SquareTerminal,
  SunMoon,
  WifiOff,
  X,
} from "lucide-react";
import {
  Alert,
  Badge,
  Button,
  Dialog,
  Input,
  Panel,
  Skeleton,
} from "./components/ui";
import { native, unwrap } from "./bridge";
import type { Config, Operation, Snapshot } from "./types";

const navigation = [
  ["工作台", LayoutDashboard],
  ["内容库", FolderClosed],
  ["发布中心", Send],
  ["调度计划", CalendarClock],
  ["评论互动", MessageSquare],
  ["平台账号", Settings2],
  ["运行日志", SquareTerminal],
] as const;
const operations: Record<Operation, string> = {
  "token-only": "验证凭据",
  "probe-read-only": "检查草稿权限",
  execute: "创建这一篇草稿",
  reconcile: "核对已有草稿",
};
const empty: Config = {
  account_name: "",
  app_id: "",
  app_secret: "",
  access_token: "",
  thumb_media_id: "",
};
function time(value: string | null) {
  return value
    ? new Date(value).toLocaleString("zh-CN", { hour12: false })
    : "尚无记录";
}
export function App() {
  const [page, setPage] = useState("平台账号"),
    [snapshot, setSnapshot] = useState<Snapshot | null>(null),
    [error, setError] = useState(""),
    [toast, setToast] = useState(""),
    [loading, setLoading] = useState(true),
    [saving, setSaving] = useState(false),
    [config, setConfig] = useState<Config>(empty),
    [advanced, setAdvanced] = useState(false),
    [editingConfig, setEditingConfig] = useState(false),
    [operation, setOperation] = useState<Operation | null>(null),
    [confirmed, setConfirmed] = useState(false),
    [starting, setStarting] = useState(false),
    [theme, setTheme] = useState(
      () => localStorage.getItem("rancho-theme") || "system",
    );
  const load = useCallback(async (initial = false) => {
    try {
      const value = await unwrap(native().get_snapshot());
      setSnapshot(value);
      setError("");
      if (initial)
        setConfig({
          ...empty,
          account_name: value.config.account_name,
          app_id: value.config.app_id,
          thumb_media_id: value.config.thumb_media_id,
        });
    } catch (e) {
      setError(e instanceof Error ? e.message : "读取本机状态失败。");
    } finally {
      setLoading(false);
    }
  }, []);
  useEffect(() => {
    let timer: number;
    function ready() {
      void load(true);
    }
    window.addEventListener("pywebviewready", ready);
    if (window.pywebview) ready();
    else
      timer = window.setTimeout(() => {
        if (!window.pywebview) {
          setLoading(false);
          setError("未连接 Windows 宿主。浏览器预览不提供凭据保存或平台操作。");
        }
      }, 4000);
    return () => {
      clearTimeout(timer);
      window.removeEventListener("pywebviewready", ready);
    };
  }, [load]);
  useEffect(() => {
    const media = matchMedia("(prefers-color-scheme: dark)");
    function apply() {
      document.documentElement.dataset.theme =
        theme === "system" ? (media.matches ? "dark" : "light") : theme;
    }
    apply();
    localStorage.setItem("rancho-theme", theme);
    media.addEventListener("change", apply);
    return () => media.removeEventListener("change", apply);
  }, [theme]);
  useEffect(() => {
    if (!snapshot?.run.running) return;
    const timer = setInterval(() => void load(), 1200);
    return () => clearInterval(timer);
  }, [snapshot?.run.running, load]);
  useEffect(() => {
    if (!toast) return;
    const timer = setTimeout(() => setToast(""), 4500);
    return () => clearTimeout(timer);
  }, [toast]);
  async function save() {
    setSaving(true);
    setError("");
    try {
      setSnapshot(await unwrap(native().save_config(config)));
      setConfig((value) => ({ ...value, app_secret: "", access_token: "" }));
      setEditingConfig(false);
      setToast("配置已使用 Windows DPAPI 加密保存。未调用微信接口。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "配置保存失败。");
    } finally {
      setSaving(false);
    }
  }
  async function choose() {
    try {
      setSnapshot(await unwrap(native().choose_cover()));
      setToast("封面已选择。保存配置后可在下次启动继续使用。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "无法读取封面。");
    }
  }
  async function start() {
    if (!operation || !snapshot || !confirmed) return;
    setStarting(true);
    try {
      const c = snapshot.config;
      setSnapshot(
        await unwrap(
          native().start_operation({
            operation,
            confirmed,
            app_id_sha256: c.app_id_sha256,
            variant_sha256: c.variant_sha256,
            cover_sha256: c.cover_sha256,
          }),
        ),
      );
      setOperation(null);
      setToast("已启动单次任务。不会自动重试。");
    } catch (e) {
      setError(e instanceof Error ? e.message : "任务启动失败。");
      setOperation(null);
    } finally {
      setStarting(false);
    }
  }
  function ask(value: Operation) {
    setConfirmed(false);
    setOperation(value);
  }
  const c = snapshot?.config,
    r = snapshot?.receipt,
    busy = Boolean(snapshot?.run.running || snapshot?.run.locked),
    networkDisabled = Boolean(snapshot?.network_disabled),
    ready = Boolean(c?.has_secret && c.sdk_ready),
    remoteDisabled = !ready || busy || networkDisabled;
  const connected = r?.token_state === "CONFIRMED";
  const verified = connected && r?.permission_verified;
  const activeStep = snapshot?.run.running
    ? snapshot.run.operation === "token-only"
      ? 1
      : snapshot.run.operation === "probe-read-only"
        ? 2
        : r?.draft_state === "CONFIRMED"
          ? 4
          : 3
    : -1;
  const field = (
    key: keyof Config,
    label: string,
    secret = false,
    hint?: string,
  ) => (
    <div className="field" key={key}>
      <label htmlFor={key}>
        {label}
        {secret && <LockKeyhole size={12} />}
      </label>
      <Input
        id={key}
        type={secret ? "password" : "text"}
        value={config[key]}
        autoComplete="off"
        spellCheck={false}
        placeholder={
          secret
            ? key === "app_secret" && c?.has_secret
              ? "已安全保存 · 留空保持原值"
              : "仅在本机输入"
            : key === "app_id"
              ? "wx…"
              : undefined
        }
        onChange={(event) =>
          setConfig({ ...config, [key]: event.target.value })
        }
      />
      {hint && <p className="field-hint">{hint}</p>}
    </div>
  );
  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-symbol">R</div>
          <div>
            <strong>RanchoMedia</strong>
            <span>内容工作空间</span>
          </div>
        </div>
        <div className="workspace-switch">
          <div className="workspace-icon">兰</div>
          <div>
            <strong>兰澈的工作空间</strong>
            <span>本地 · Windows</span>
          </div>
          <ChevronDown size={13} />
        </div>
        <nav aria-label="主导航">
          {navigation.map(([label, Icon]) => (
            <button
              key={label}
              className={`nav-item ${page === label ? "active" : ""}`}
              aria-current={page === label ? "page" : undefined}
              onClick={() => {
                setPage(label);
                setError("");
              }}
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
              {label === "平台账号" && verified && (
                <span className="nav-indicator" />
              )}
            </button>
          ))}
        </nav>
        <div className="sidebar-footer">
          <div className="local-indicator">
            <span />
            本机运行 · 草稿模式
          </div>
          <label className="theme-select">
            <SunMoon size={14} />
            <select
              aria-label="外观主题"
              value={theme}
              onChange={(event) => setTheme(event.target.value)}
            >
              <option value="system">跟随系统</option>
              <option value="light">浅色</option>
              <option value="dark">深色</option>
            </select>
          </label>
          <span className="version">Desktop 2.0 · Preview</span>
        </div>
      </aside>
      <main className="main">
        <header className="topbar">
          <div className="breadcrumb">
            工作空间
            <ChevronRight size={12} />
            <strong>{page}</strong>
          </div>
          <div className="topbar-right">
            <span className="optional small muted">
              <Monitor size={13} /> Windows 本地
            </span>
            <Badge tone="neutral">禁止公开发布</Badge>
            <Button
              variant="ghost"
              size="icon"
              aria-label="刷新本机状态"
              onClick={() => void load()}
            >
              <RefreshCw
                size={15}
                className={snapshot?.run.running ? "spin" : ""}
              />
            </Button>
          </div>
        </header>
        <div className="page">
          <div className="page-header">
            <div>
              <div className="eyebrow">
                {page === "平台账号" ? "CONNECTIONS" : "WORKSPACE"}
              </div>
              <h1>{page}</h1>
              <p>
                {page === "平台账号"
                  ? "管理平台凭据、接口权限和草稿接入。"
                  : page === "内容库"
                    ? "保留现有稿件、审核状态与版本关系。"
                    : "来自本机工作空间的实际记录。"}
              </p>
            </div>
            {page === "平台账号" && (
              <Button
                variant="outline"
                size="sm"
                onClick={() =>
                  void unwrap(native().open_docs("account")).catch(() =>
                    setError("无法打开帮助页面。"),
                  )
                }
              >
                <CircleHelp size={14} />
                公众号后台
              </Button>
            )}
          </div>
          {error && (
            <div className="notice">
              <Alert tone="danger" title="操作未完成">
                {error}
              </Alert>
            </div>
          )}
          {networkDisabled && (
            <div className="notice">
              <Alert tone="info" title="隔离验收模式">
                本次应用会话已禁用所有远端操作。界面读取现有回执，配置仍使用真实
                Windows DPAPI。
              </Alert>
            </div>
          )}
          {loading ? (
            <Skeleton />
          ) : page === "平台账号" ? (
            <>
              <div className="account-tabs">
                <button className="selected" aria-current="page">
                  微信公众号
                </button>
                <button
                  onClick={() =>
                    setToast("小红书已有独立接入记录；本阶段桌面页尚未接入。")
                  }
                >
                  小红书<Badge>待接入桌面</Badge>
                </button>
                <button
                  onClick={() =>
                    setToast("抖音已有独立接入记录；本阶段桌面页尚未接入。")
                  }
                >
                  抖音<Badge>待接入桌面</Badge>
                </button>
              </div>
              <section
                className={`connection-overview ${verified ? "verified" : ""}`}
                aria-label="当前微信连接状态"
              >
                <div className="connection-heading">
                  <div className="connection-icon">
                    <ShieldCheck size={24} />
                  </div>
                  <div>
                    <div className="overline">当前连接 · 微信公众号</div>
                    <h2>
                      {snapshot?.run.running
                        ? "正在进行单次验证"
                        : verified
                          ? "已连接，草稿读取可用"
                          : connected
                            ? "凭据已验证，待读取权限"
                            : "连接待验证"}
                    </h2>
                    <p>
                      {c?.account_name || "尚未配置公众号"} ·{" "}
                      {verified
                        ? "下一步需单独确认一篇稿件的写入授权"
                        : "按步骤验证配置与接口权限"}
                    </p>
                  </div>
                  <Badge tone={verified ? "success" : "neutral"}>
                    {verified ? "已取得真实回执" : "等待验证"}
                  </Badge>
                </div>
                <div className="connection-metrics">
                  <div>
                    <span>访问令牌</span>
                    <strong>{connected ? "已验证" : "待验证"}</strong>
                    <small>{time(r?.token_time || null)}</small>
                  </div>
                  <div>
                    <span>草稿读取</span>
                    <strong>
                      {r?.permission_verified ? "接口可用" : "尚未确认"}
                    </strong>
                    <small>{time(r?.permission_time || null)}</small>
                  </div>
                  <div>
                    <span>最近 HTTP 响应</span>
                    <strong>
                      {r?.http_status || "—"}
                      <em>
                        {r?.elapsed_ms != null
                          ? `${Math.round(r.elapsed_ms)} ms`
                          : ""}
                      </em>
                    </strong>
                    <small>
                      {r?.network_mode === "ENVIRONMENT_PROXY"
                        ? "使用已选择的环境代理"
                        : "使用直连路径"}
                    </small>
                  </div>
                </div>
              </section>
              {busy && (
                <div className="run-banner" role="status" aria-live="polite">
                  <Activity
                    size={17}
                    className={snapshot?.run.running ? "pulse" : ""}
                  />
                  <div>
                    <strong>
                      {snapshot?.run.running
                        ? operations[snapshot.run.operation as Operation] ||
                          "正在执行"
                        : "执行锁等待核对"}
                    </strong>
                    <span>
                      {snapshot?.run.running
                        ? "等待真实接口回执；不会自动重试或追加操作。"
                        : "请核对上次任务与进程，应用不会重复发送。"}
                    </span>
                  </div>
                  <time>{time(snapshot?.run.started_at || null)}</time>
                </div>
              )}
              <div className="page-layout">
                <div className="column">
                  <Panel
                    title="公众号连接"
                    description="凭据只在此 Windows 账户下解密，不回传至界面。"
                    action={
                      <Button
                        variant="ghost"
                        size="sm"
                        onClick={() => setEditingConfig(!editingConfig)}
                      >
                        <Settings2 size={14} />
                        {editingConfig ? "收起配置" : "编辑配置"}
                      </Button>
                    }
                  >
                    {c?.encrypted && !editingConfig ? (
                      <div className="saved-config">
                        <LockKeyhole size={18} />
                        <div>
                          <strong>{c.account_name || "公众号凭据"}</strong>
                          <span>
                            已使用 Windows DPAPI 加密保存 · 敏感字段不会回填
                          </span>
                        </div>
                        <Badge tone="success">已安全保存</Badge>
                      </div>
                    ) : (
                      <form
                        onSubmit={(event) => {
                          event.preventDefault();
                          void save();
                        }}
                      >
                        <div className="panel-body">
                          <div className="form-grid">
                            {field(
                              "account_name",
                              "公众号名称",
                              false,
                              "填写后台中你已核实的公众号名称。",
                            )}
                            {field(
                              "app_id",
                              "AppID",
                              false,
                              "公众号后台 → 设置与开发 → 基本配置。",
                            )}
                            {field(
                              "app_secret",
                              "AppSecret",
                              true,
                              "留空保留已存凭据；更换 Secret 会清除旧令牌。",
                            )}
                            <div className="field">
                              <label>安全存储</label>
                              <div
                                className="security-note"
                                style={{ paddingTop: 7 }}
                              >
                                <ShieldCheck size={16} />
                                <span>
                                  Windows DPAPI · 当前用户
                                  <br />
                                  不保存到浏览器，不写入日志。
                                </span>
                              </div>
                            </div>
                          </div>
                          {c?.credential_error && (
                            <Alert tone="danger" title="凭据不可用">
                              {c.credential_error}
                            </Alert>
                          )}
                          <button
                            className="advanced-toggle"
                            type="button"
                            onClick={() => setAdvanced(!advanced)}
                          >
                            <ChevronRight
                              size={13}
                              style={{
                                transform: advanced
                                  ? "rotate(90deg)"
                                  : undefined,
                              }}
                            />
                            高级配置：已有令牌与封面 media_id
                          </button>
                          {advanced && (
                            <div className="form-grid">
                              {field(
                                "access_token",
                                "已有 access_token（可选）",
                                true,
                                "不要求从聊天发送令牌；优先使用 AppSecret 验证。",
                              )}
                              {field(
                                "thumb_media_id",
                                "已有永久封面 media_id（可选）",
                                false,
                                "必须属于当前公众号；不填则在建草稿时上传本地封面。",
                              )}
                            </div>
                          )}
                        </div>
                        <div className="panel-footer">
                          <span className="security-note">
                            <LockKeyhole size={13} />
                            保存配置不会请求微信接口
                          </span>
                          <Button
                            type="submit"
                            loading={saving}
                            disabled={busy}
                          >
                            保存配置
                          </Button>
                        </div>
                      </form>
                    )}
                  </Panel>
                  <Panel
                    title="接口检查"
                    description="独立验证访问令牌与草稿读取权限，每次操作均需确认。"
                  >
                    <div className="status-list">
                      <div className="status-row">
                        <span>SDK 执行环境</span>
                        <Badge tone={c?.sdk_ready ? "success" : "warning"}>
                          {c?.sdk_ready
                            ? "已安装 · 固定版本"
                            : "缺少已固定的 SDK 环境"}
                        </Badge>
                      </div>
                      <div className="status-row">
                        <span>账号凭据</span>
                        <Badge
                          tone={
                            r?.token_state === "CONFIRMED"
                              ? "success"
                              : "warning"
                          }
                        >
                          {r?.token_state === "CONFIRMED"
                            ? "已验证令牌"
                            : c?.has_secret
                              ? "已保存 · 等待验证"
                              : "等待配置"}
                        </Badge>
                      </div>
                      <div className="status-row">
                        <span>草稿接口权限</span>
                        <Badge
                          tone={r?.permission_verified ? "success" : "neutral"}
                        >
                          {r?.permission_verified
                            ? "已读取远端回执"
                            : "尚未确认"}
                        </Badge>
                      </div>
                    </div>
                    <div className="panel-body" style={{ paddingTop: 6 }}>
                      {r?.errcode ? (
                        <Alert
                          title={`最近一次微信回执 · ${r.errcode}`}
                          tone="warning"
                        >
                          {r.message}
                          <br />
                          <span>
                            记录时间：{time(r.time)}
                            。这是已有回执，尚未重新验证。
                          </span>
                        </Alert>
                      ) : verified ? (
                        <Alert tone="success" title="当前连接验证通过">
                          访问令牌和草稿读取均取得真实回执。素材上传与草稿创建仍需单独授权。
                        </Alert>
                      ) : (
                        <Alert tone="info" title="核对请求路径">
                          以微信回执中的出口 IP
                          核对代理分流与白名单，应用不会自动修改网络配置。
                        </Alert>
                      )}
                      <p className="field-hint" style={{ marginTop: 12 }}>
                        API IP 白名单：在公众号官方后台的「开发密钥 / API IP
                        白名单」中， 加入微信请求实际使用的公网出口
                        IP；保存后手动验证凭据。
                        应用不会修改白名单，也不会自动重试。
                      </p>
                      <div className="run-actions">
                        <Button
                          variant="outline"
                          disabled={remoteDisabled || r?.unknown}
                          onClick={() => ask("token-only")}
                        >
                          <ShieldCheck size={14} />
                          验证凭据
                        </Button>
                        <Button
                          variant="ghost"
                          disabled={remoteDisabled || r?.unknown}
                          onClick={() => ask("probe-read-only")}
                        >
                          检查草稿权限
                          <ArrowRight size={13} />
                        </Button>
                      </div>
                      {Boolean(r?.history?.length) && (
                        <details className="history-receipts">
                          <summary>
                            历史失败回执{" "}
                            <span>
                              {r?.history.length} 条记录 · 不代表当前连接状态
                            </span>
                          </summary>
                          {r?.history.map((item, index) => (
                            <div
                              className="history-row"
                              key={`${item.time}-${index}`}
                            >
                              <Badge tone="warning">{item.errcode}</Badge>
                              <div>
                                <strong>{time(item.time)}</strong>
                                <p>{item.message}</p>
                              </div>
                            </div>
                          ))}
                        </details>
                      )}
                    </div>
                  </Panel>
                  <Panel
                    title="准备一篇草稿"
                    description="使用现有稿件，封面与内容均绑定本次确认；不触发公开发表。"
                  >
                    <div className="panel-body">
                      <div className="workflow">
                        {[
                          "保存配置",
                          "验证凭据",
                          "检查权限",
                          "封面 / 草稿",
                          "核对回执",
                        ].map((label, index) => {
                          const done = [
                            Boolean(c?.encrypted),
                            r?.token_state === "CONFIRMED",
                            Boolean(r?.permission_verified),
                            r?.draft_state === "CONFIRMED",
                            Boolean(
                              r?.draft_media_id &&
                              r.status === "REMOTE_DRAFT_CONFIRMED",
                            ),
                          ][index];
                          return (
                            <div
                              key={label}
                              className={`step ${done ? "done" : ""} ${activeStep === index ? "active" : ""}`}
                              aria-current={
                                activeStep === index ? "step" : undefined
                              }
                            >
                              <span className="step-circle">
                                {done ? <Check size={13} /> : index + 1}
                              </span>
                              {label}
                            </div>
                          );
                        })}
                      </div>
                      <div className="file-row">
                        <div className="file-label">
                          <FileText size={20} />
                          <div>
                            <strong>
                              {c?.article_title || "未找到已准备稿件"}
                            </strong>
                            <p>
                              现有单稿 · 内容指纹{" "}
                              {c?.variant_sha256?.slice(0, 12) || "不可用"}
                            </p>
                          </div>
                        </div>
                        <Badge>仅创建草稿</Badge>
                      </div>
                      <div className="file-row">
                        <div className="file-label">
                          <ImagePlus size={20} />
                          <div>
                            <strong>{c?.cover_name || "尚未选择封面"}</strong>
                            <p>PNG / JPEG · 不超过 2 MB · 远端需验证图片规格</p>
                          </div>
                        </div>
                        <Button
                          variant="outline"
                          size="sm"
                          disabled={busy}
                          onClick={() => void choose()}
                        >
                          选择封面
                        </Button>
                      </div>
                      {r?.unknown && (
                        <div style={{ marginTop: 14 }}>
                          <Alert
                            tone="danger"
                            title="上次结果不明，禁止重复写入"
                          >
                            请在公众号后台确认是否已建草稿；有远端 ID
                            时可以核对。应用不会自动重发上传或 draft/add。
                          </Alert>
                        </div>
                      )}
                      <div className="run-actions">
                        <Button
                          disabled={
                            remoteDisabled ||
                            r?.unknown ||
                            !c?.variant_sha256 ||
                            !c.cover_sha256
                          }
                          loading={snapshot?.run.running}
                          onClick={() => ask("execute")}
                        >
                          创建草稿
                          <ArrowRight size={14} />
                        </Button>
                        <Button
                          variant="outline"
                          disabled={remoteDisabled || !r?.draft_media_id}
                          onClick={() => ask("reconcile")}
                        >
                          核对已有草稿
                        </Button>
                        <span className="field-hint">
                          单次执行 · 不自动重试
                        </span>
                      </div>
                      {busy && (
                        <p className="field-hint" style={{ marginTop: 12 }}>
                          <Activity size={12} />{" "}
                          {snapshot?.run.running
                            ? "正在执行，请等待远端回执。"
                            : "检测到未释放锁，请人工核对进程与上次回执。"}
                        </p>
                      )}
                    </div>
                  </Panel>
                </div>
                <aside className="aside column">
                  <Panel title="当前授权范围">
                    <div className="aside-body">
                      <ul className="scope-list">
                        <li className="allowed">
                          <Check size={14} />
                          验证账号与读取草稿
                        </li>
                        <li className="blocked">
                          <LockKeyhole size={14} />
                          封面上传 · 待单稿授权
                        </li>
                        <li className="blocked">
                          <LockKeyhole size={14} />
                          草稿创建 · 待单稿授权
                        </li>
                        <li className="blocked">
                          <X size={14} />
                          公开发表 / 群发
                        </li>
                        <li className="blocked">
                          <X size={14} />
                          自动评论与回复
                        </li>
                      </ul>
                      <div className="divider" />
                      <p className="small muted">
                        每次远端操作都需要确认。未获得回执不代表成功。
                      </p>
                    </div>
                  </Panel>
                  <Panel title="执行回执">
                    <div className="aside-body">
                      <dl className="receipt-meta">
                        <div>
                          <dt>最新状态</dt>
                          <dd className="mono">
                            {snapshot?.run.running
                              ? "RUNNING"
                              : r?.status || "NOT_RUN"}
                          </dd>
                        </div>
                        <div>
                          <dt>远端草稿 ID</dt>
                          <dd className="mono">
                            {r?.draft_media_id || "尚无远端 ID"}
                          </dd>
                        </div>
                        <div>
                          <dt>记录时间</dt>
                          <dd>{time(r?.time || null)}</dd>
                        </div>
                        <div>
                          <dt>后续动作</dt>
                          <dd>
                            {r?.errcode === 40164
                              ? "核对微信实际出口与代理分流"
                              : r?.unknown
                                ? "人工核对远端状态"
                                : verified
                                  ? "确认单稿内容与封面，再授权写入"
                                  : "核对账号与接口权限"}
                          </dd>
                        </div>
                      </dl>
                      <div className="divider" />
                      <button
                        className="inline-link"
                        onClick={() =>
                          void unwrap(native().open_docs("draft")).catch(() =>
                            setError("无法打开帮助页面。"),
                          )
                        }
                      >
                        查看官方草稿接口说明
                        <ArrowRight size={12} />
                      </button>
                    </div>
                  </Panel>
                  <Panel title="最近操作">
                    <div className="aside-body">
                      {snapshot?.events.length ? (
                        <div className="log-list">
                          {snapshot.events
                            .slice(-5)
                            .reverse()
                            .map((event, index) => (
                              <div className="log-row" key={index}>
                                <time>
                                  {new Date(event.time).toLocaleTimeString(
                                    "zh-CN",
                                    { hour12: false },
                                  )}
                                </time>
                                <span>
                                  {event.action === "CONFIG_ENCRYPTED"
                                    ? "配置已加密保存"
                                    : event.action === "RUN_STARTED"
                                      ? "单次任务已启动"
                                      : event.code}
                                </span>
                              </div>
                            ))}
                        </div>
                      ) : (
                        <p className="small muted">
                          还没有桌面端操作记录。已有平台回执保留在上方。
                        </p>
                      )}
                    </div>
                  </Panel>
                </aside>
              </div>
            </>
          ) : (
            <Workspace page={page} snapshot={snapshot} />
          )}
        </div>
      </main>
      {toast && (
        <div className="toast" role="status">
          {toast}
        </div>
      )}
      <Dialog
        open={operation !== null}
        onOpenChange={(value) => {
          if (!value) setOperation(null);
        }}
        title={operation ? operations[operation] : ""}
        description={
          operation === "execute"
            ? "本次会使用官方 SDK 上传封面并创建一篇微信草稿。不会公开发表或群发。"
            : operation === "token-only"
              ? "本次只请求一次微信令牌，不上传封面或创建草稿。"
              : "本次读取微信草稿接口，不创建新的内容。"
        }
      >
        <div className="confirm-scope">
          <span>
            账号：{c?.account_name || "未填写"} · {c?.app_id || "未配置"}
          </span>
          {operation === "execute" && <span>稿件：{c?.article_title}</span>}
          <span>
            执行范围：{operation ? operations[operation] : ""} · 不自动重试
          </span>
        </div>
        <label className="confirm-checkbox">
          <input
            type="checkbox"
            checked={confirmed}
            onChange={(event) => setConfirmed(event.target.checked)}
          />
          我已核对账号、IP 白名单与本次操作范围，同意执行这一次操作。
        </label>
        <div className="dialog-actions">
          <Button variant="outline" onClick={() => setOperation(null)}>
            取消
          </Button>
          <Button
            disabled={!confirmed}
            loading={starting}
            onClick={() => void start()}
          >
            确认本次执行
          </Button>
        </div>
      </Dialog>
    </div>
  );
}

function Workspace({
  page,
  snapshot,
}: {
  page: string;
  snapshot: Snapshot | null;
}) {
  const articles = snapshot?.workspace.articles || [],
    tasks = snapshot?.workspace.tasks || [];
  return (
    <div className="column">
      {page === "工作台" && (
        <div className="stats">
          <div className="stat">
            <small>本地内容</small>
            <strong>{articles.length}</strong>
          </div>
          <div className="stat">
            <small>等待作者审核</small>
            <strong>
              {articles.filter((a) => a.status === "IN_REVIEW").length}
            </strong>
          </div>
          <div className="stat">
            <small>当前运行</small>
            <strong>{snapshot?.run.running ? "1" : "0"}</strong>
          </div>
        </div>
      )}
      <Alert tone="info" title="只读工作区">
        {page === "运行日志"
          ? "展示桌面操作记录，不输出敏感参数。"
          : `此页读取现有数据。${page === "内容库" ? "正文编辑与版本比较请继续使用原有编辑台。" : "新的操作入口将在后续阶段接入。"}`}{" "}
        数据库：{snapshot?.workspace.database_status || "未连接"}。
      </Alert>
      {["内容库", "工作台"].includes(page) ? (
        <Panel
          title="现有内容"
          description="沿用原有审核门禁；本页面不会批准或修改稿件。"
        >
          {articles.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>标题</th>
                  <th>审核状态</th>
                  <th>操作范围</th>
                </tr>
              </thead>
              <tbody>
                {articles.map((article) => (
                  <tr key={article.id}>
                    <td>{article.title}</td>
                    <td>
                      <Badge tone="warning">{article.status}</Badge>
                    </td>
                    <td className="muted">只读</td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">没有可读取的内容记录。</div>
          )}
        </Panel>
      ) : page === "调度计划" ? (
        <Panel title="已有持久化任务">
          {tasks.length ? (
            <table className="table">
              <thead>
                <tr>
                  <th>类型</th>
                  <th>状态</th>
                </tr>
              </thead>
              <tbody>
                {tasks.map((task) => (
                  <tr key={task.id}>
                    <td>{task.kind}</td>
                    <td>
                      <Badge>{task.status}</Badge>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          ) : (
            <div className="empty">
              没有可读取的任务。桌面端未启动新的定时任务。
            </div>
          )}
        </Panel>
      ) : page === "运行日志" ? (
        <Panel title="本机操作审计">
          <div className="panel-body">
            {snapshot?.events.length ? (
              snapshot.events
                .slice()
                .reverse()
                .map((event, index) => (
                  <div className="log-row" key={index}>
                    <time>
                      {new Date(event.time).toLocaleTimeString("zh-CN", {
                        hour12: false,
                      })}
                    </time>
                    <span>
                      {event.action} · {event.code}
                    </span>
                  </div>
                ))
            ) : (
              <div className="empty">尚无桌面操作记录。</div>
            )}
          </div>
        </Panel>
      ) : (
        <Panel
          title={page === "评论互动" ? "评论功能待接入" : "发布操作待接入"}
        >
          <div className="empty">
            <WifiOff size={26} />
            {page === "评论互动"
              ? "尚未接入评论读取。回复仍需要逐条授权。"
              : "本阶段仅提供公众号草稿接入。此处不会发起发布。"}
            <br />
            没有新增远端操作或模拟成功数据。
          </div>
        </Panel>
      )}
    </div>
  );
}
