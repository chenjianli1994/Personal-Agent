export class ApiError extends Error {
  status: number;
  code?: string;
  detail?: unknown;

  constructor(status: number, message: string, code?: string, detail?: unknown) {
    super(message);
    this.status = status;
    this.code = code;
    this.detail = detail;
  }
}

function withTimeout(signal?: AbortSignal, timeoutMs = 60000): { signal?: AbortSignal; cleanup: () => void } {
  if (timeoutMs <= 0) {
    return { signal, cleanup: () => undefined };
  }
  const controller = new AbortController();
  const timer = window.setTimeout(() => {
    controller.abort(new DOMException("请求超时", "TimeoutError"));
  }, timeoutMs);
  if (signal) {
    signal.addEventListener("abort", () => controller.abort(signal.reason), { once: true });
  }
  return {
    signal: controller.signal,
    cleanup: () => window.clearTimeout(timer),
  };
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const data = text ? safeJson(text) : null;
  if (!response.ok) {
    const detail = data?.detail?.error ?? data?.detail ?? data?.error;
    const message = typeof detail === "string" ? detail : detail?.message ?? response.statusText;
    throw new ApiError(response.status, message, detail?.code, detail);
  }
  return data as T;
}

function safeJson(text: string): any {
  try {
    return JSON.parse(text);
  } catch {
    return text;
  }
}

export async function apiGet<T>(path: string, init?: { signal?: AbortSignal; timeoutMs?: number }): Promise<T> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs);
  try {
    const response = await fetch(path, { credentials: "same-origin", signal });
    return parseResponse<T>(response);
  } finally {
    cleanup();
  }
}

export async function apiText(path: string, init?: { signal?: AbortSignal; timeoutMs?: number }): Promise<string> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs);
  try {
    const response = await fetch(path, { credentials: "same-origin", signal });
    if (!response.ok) {
      throw new ApiError(response.status, response.statusText);
    }
    return response.text();
  } finally {
    cleanup();
  }
}

export async function apiPost<TReq, TRes>(
  path: string,
  body?: TReq,
  init?: { signal?: AbortSignal; timeoutMs?: number },
): Promise<TRes> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs);
  try {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    return parseResponse<TRes>(response);
  } finally {
    cleanup();
  }
}

export async function apiPostForm<TRes>(
  path: string,
  body: FormData,
  init?: { signal?: AbortSignal; timeoutMs?: number },
): Promise<TRes> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs);
  try {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      body,
      signal,
    });
    return parseResponse<TRes>(response);
  } finally {
    cleanup();
  }
}

export async function apiPut<TReq, TRes>(
  path: string,
  body?: TReq,
  init?: { signal?: AbortSignal; timeoutMs?: number },
): Promise<TRes> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs);
  try {
    const response = await fetch(path, {
      method: "PUT",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: body === undefined ? undefined : JSON.stringify(body),
      signal,
    });
    return parseResponse<TRes>(response);
  } finally {
    cleanup();
  }
}

export async function apiDelete<TRes>(path: string, init?: { signal?: AbortSignal; timeoutMs?: number }): Promise<TRes> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs);
  try {
    const response = await fetch(path, { method: "DELETE", credentials: "same-origin", signal });
    return parseResponse<TRes>(response);
  } finally {
    cleanup();
  }
}

export async function apiStream(
  path: string,
  body: unknown,
  onEvent: (event: any) => void,
  init?: { signal?: AbortSignal; timeoutMs?: number },
): Promise<void> {
  const { signal, cleanup } = withTimeout(init?.signal, init?.timeoutMs ?? 0);
  try {
    const response = await fetch(path, {
      method: "POST",
      credentials: "same-origin",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(body),
      signal,
    });
    if (!response.ok || !response.body) {
      throw new ApiError(response.status, response.statusText);
    }
    const reader = response.body.getReader();
    const decoder = new TextDecoder();
    let buffer = "";
    for (;;) {
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });
      let boundary = buffer.indexOf("\n\n");
      while (boundary >= 0) {
        const frame = buffer.slice(0, boundary).trim();
        buffer = buffer.slice(boundary + 2);
        if (frame.startsWith("data:")) {
          onEvent(JSON.parse(frame.slice(5).trim()));
        }
        boundary = buffer.indexOf("\n\n");
      }
    }
  } finally {
    cleanup();
  }
}

export function apiUpload<TRes>(
  path: string,
  body: FormData,
  onProgress?: (percent: number) => void,
): Promise<TRes> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", path);
    xhr.withCredentials = true;
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable && onProgress) {
        onProgress(Math.round((event.loaded / event.total) * 100));
      }
    };
    xhr.onerror = () => reject(new ApiError(0, "network error"));
    xhr.onload = () => {
      try {
        const data = xhr.responseText ? safeJson(xhr.responseText) : null;
        if (xhr.status >= 200 && xhr.status < 300) {
          resolve(data as TRes);
          return;
        }
        const detail = data?.detail?.error ?? data?.detail ?? data?.error;
        const message = typeof detail === "string" ? detail : detail?.message ?? xhr.statusText;
        reject(new ApiError(xhr.status, message, detail?.code, detail));
      } catch (error) {
        reject(error);
      }
    };
    xhr.send(body);
  });
}
