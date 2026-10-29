import type { Envelope, NativeAPI } from "./types";
export function native(): NativeAPI {
  if (!window.pywebview?.api)
    throw new Error("未连接 Windows 宿主。请使用桌面启动器打开应用。");
  return window.pywebview.api;
}
export async function unwrap<T>(result: Promise<Envelope<T>>): Promise<T> {
  const response = await result;
  if (!response.ok)
    throw new Error(`${response.error.message} [${response.error.code}]`);
  return response.data;
}
