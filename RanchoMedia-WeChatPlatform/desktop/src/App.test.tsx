import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { App } from "./App";
import type { NativeAPI, Snapshot } from "./types";

const snapshot: Snapshot = {
  config: {
    account_name: "合成测试账号",
    app_id: "wx0123456789abcdef",
    app_id_sha256: "account-hash",
    has_secret: true,
    has_token: false,
    thumb_media_id: "",
    encrypted: true,
    credential_error: null,
    cover_name: "cover.png",
    cover_sha256: "cover-hash",
    article_title: "已有测试稿件",
    variant_sha256: "article-hash",
    sdk_ready: true,
  },
  receipt: {
    status: "REMOTE_REJECTED",
    errcode: 40164,
    message: "请配置 API IP 白名单。",
    time: "2026-10-29T01:00:00Z",
    draft_media_id: null,
    token_state: "REJECTED",
    cover_state: null,
    draft_state: null,
    unknown: false,
    permission_verified: false,
    token_time: null,
    permission_time: null,
    http_status: null,
    elapsed_ms: null,
    network_mode: "DIRECT",
    history: [],
  },
  run: {
    running: false,
    locked: false,
    operation: null,
    status: null,
    started_at: null,
    finished_at: null,
  },
  events: [],
  network_disabled: false,
  workspace: {
    database_status: "READ_ONLY",
    articles: [{ id: "a", title: "原有待审文章", status: "IN_REVIEW" }],
    tasks: [],
  },
};
let api: NativeAPI;
beforeEach(() => {
  localStorage.clear();
  api = {
    get_snapshot: vi
      .fn()
      .mockResolvedValue({ ok: true, data: structuredClone(snapshot) }),
    save_config: vi
      .fn()
      .mockResolvedValue({ ok: true, data: structuredClone(snapshot) }),
    choose_cover: vi
      .fn()
      .mockResolvedValue({ ok: true, data: structuredClone(snapshot) }),
    start_operation: vi
      .fn()
      .mockResolvedValue({ ok: true, data: structuredClone(snapshot) }),
    open_docs: vi.fn().mockResolvedValue({ ok: true, data: { opened: true } }),
  };
  window.pywebview = { api };
});
afterEach(() => {
  cleanup();
  delete window.pywebview;
  vi.useRealTimers();
});
async function setup() {
  render(<App />);
  await screen.findByText("合成测试账号");
  const edit = screen.queryByRole("button", { name: "编辑配置" });
  if (edit) fireEvent.click(edit);
  await screen.findByDisplayValue("合成测试账号");
}
describe("native desktop UI", () => {
  it("uses real host state without requesting any platform operation", async () => {
    await setup();
    expect(screen.getByText("最近一次微信回执 · 40164")).toBeVisible();
    expect(screen.getByText("尚无远端 ID")).toBeVisible();
    expect(api.start_operation).not.toHaveBeenCalled();
    expect(api.save_config).not.toHaveBeenCalled();
  });
  it("never persists a typed secret and clears it after encrypted save", async () => {
    await setup();
    fireEvent.change(screen.getByLabelText("AppSecret"), {
      target: { value: "synthetic-ui-secret" },
    });
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await screen.findByText(/配置已使用 Windows DPAPI 加密保存/);
    fireEvent.click(screen.getByRole("button", { name: "编辑配置" }));
    expect(screen.getByLabelText("AppSecret")).toHaveValue("");
    expect(api.save_config).toHaveBeenCalledWith(
      expect.objectContaining({ app_secret: "synthetic-ui-secret" }),
    );
    expect(JSON.stringify(localStorage)).not.toContain("synthetic-ui-secret");
    expect(sessionStorage.length).toBe(0);
  });
  it("shows IPC failure without pretending configuration succeeded", async () => {
    api.save_config = vi.fn().mockResolvedValue({
      ok: false,
      error: { code: "DPAPI_UNAVAILABLE", message: "Windows 凭据不可用" },
    });
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "保存配置" }));
    await screen.findByText(/Windows 凭据不可用/);
    expect(
      screen.queryByText(/配置已使用 Windows DPAPI 加密保存/),
    ).not.toBeInTheDocument();
  });
  it("requires explicit single operation confirmation and binds content hashes", async () => {
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "创建草稿" }));
    const dialog = screen.getByRole("dialog");
    const confirm = within(dialog).getByRole("button", {
      name: "确认本次执行",
    });
    expect(confirm).toBeDisabled();
    expect(api.start_operation).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("checkbox"));
    fireEvent.click(confirm);
    await waitFor(() =>
      expect(api.start_operation).toHaveBeenCalledWith({
        operation: "execute",
        confirmed: true,
        app_id_sha256: "account-hash",
        variant_sha256: "article-hash",
        cover_sha256: "cover-hash",
      }),
    );
  });
  it("canceling confirmation performs no remote action", async () => {
    await setup();
    fireEvent.click(screen.getByRole("button", { name: "验证凭据" }));
    fireEvent.click(screen.getByRole("button", { name: "取消" }));
    expect(api.start_operation).not.toHaveBeenCalled();
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });
  it("unknown outcome forbids another upload or draft creation", async () => {
    api.get_snapshot = vi.fn().mockResolvedValue({
      ok: true,
      data: { ...snapshot, receipt: { ...snapshot.receipt, unknown: true } },
    });
    await setup();
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeDisabled();
    expect(screen.getByText("上次结果不明，禁止重复写入")).toBeVisible();
  });
  it("isolated acceptance mode disables every remote action", async () => {
    api.get_snapshot = vi.fn().mockResolvedValue({
      ok: true,
      data: { ...snapshot, network_disabled: true },
    });
    await setup();
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "验证凭据" })).toBeDisabled();
    expect(screen.getByRole("button", { name: "保存配置" })).toBeEnabled();
  });
  it("switches theme and displays original review state read only", async () => {
    await setup();
    fireEvent.change(screen.getByLabelText("外观主题"), {
      target: { value: "dark" },
    });
    expect(document.documentElement.dataset.theme).toBe("dark");
    fireEvent.click(screen.getByRole("button", { name: "内容库" }));
    expect(screen.getByText("原有待审文章")).toBeVisible();
    expect(screen.getByText("IN_REVIEW")).toBeVisible();
    expect(
      screen.queryByRole("button", { name: "批准" }),
    ).not.toBeInTheDocument();
  });
  it("does not represent browser preview as a live desktop connection", async () => {
    delete window.pywebview;
    vi.useFakeTimers();
    render(<App />);
    await act(async () => {
      vi.advanceTimersByTime(4100);
    });
    expect(screen.getByText(/未连接 Windows 宿主/)).toBeVisible();
    expect(screen.queryByText("已加密保存")).not.toBeInTheDocument();
  });
});

describe("current connection and progress", () => {
  it("follows a system color-scheme change without saving credentials", async () => {
    let listener: (() => void) | undefined;
    const media = {
      matches: false,
      addEventListener: (_name: string, callback: () => void) => {
        listener = callback;
      },
      removeEventListener: () => {},
    };
    vi.stubGlobal("matchMedia", () => media);
    try {
      render(<App />);
      await screen.findByText("合成测试账号");
      expect(document.documentElement.dataset.theme).toBe("light");
      act(() => {
        media.matches = true;
        listener?.();
      });
      expect(document.documentElement.dataset.theme).toBe("dark");
      expect(api.save_config).not.toHaveBeenCalled();
    } finally {
      vi.unstubAllGlobals();
    }
  });
  it("keeps historical 40164 separate from current successful connection", async () => {
    api.get_snapshot = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        ...snapshot,
        receipt: {
          ...snapshot.receipt,
          status: "REMOTE_DRAFT_LIST_READ",
          errcode: null,
          token_state: "CONFIRMED",
          permission_verified: true,
          history: [
            {
              errcode: 40164,
              time: "2026-10-29T01:00:00Z",
              message: "历史出口被拒绝",
            },
          ],
        },
      },
    });
    render(<App />);
    await screen.findByText("已连接，草稿读取可用");
    expect(screen.getByText("当前连接验证通过")).toBeVisible();
    expect(
      screen.queryByText("最近一次微信回执 · 40164"),
    ).not.toBeInTheDocument();
    expect(screen.getByText("草稿创建 · 待单稿授权")).toBeVisible();
    expect(api.start_operation).not.toHaveBeenCalled();
  });
  it("marks only the actual running phase active", async () => {
    api.get_snapshot = vi.fn().mockResolvedValue({
      ok: true,
      data: {
        ...snapshot,
        run: {
          ...snapshot.run,
          running: true,
          operation: "token-only",
          started_at: "2026-10-29T02:00:00Z",
        },
      },
    });
    const { container } = render(<App />);
    await screen.findByText("正在进行单次验证");
    expect(container.querySelectorAll(".step.active")).toHaveLength(1);
    expect(container.querySelector(".step.active")).toHaveTextContent(
      "验证凭据",
    );
    expect(screen.getByRole("button", { name: "创建草稿" })).toBeDisabled();
  });
});
