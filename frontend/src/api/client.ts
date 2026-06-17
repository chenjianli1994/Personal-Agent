export class ApiError extends Error {
  status: number;
  code?: string;

  constructor(status: number, message: string, code?: string) {
    super(message);
    this.status = status;
    this.code = code;
  }
}

async function parseResponse<T>(response: Response): Promise<T> {
  const text = await response.text();
  const data = text ? safeJson(text) : null;
  if (!response.ok) {
    const detail = data?.detail?.error ?? data?.detail ?? data?.error;
    const message = typeof detail === "string" ? detail : detail?.message ?? response.statusText;
    throw new ApiError(response.status, message, detail?.code);
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

export async function apiGet<T>(path: string): Promise<T> {
  const response = await fetch(path, { credentials: "same-origin" });
  return parseResponse<T>(response);
}

export async function apiText(path: string): Promise<string> {
  const response = await fetch(path, { credentials: "same-origin" });
  if (!response.ok) {
    throw new ApiError(response.status, response.statusText);
  }
  return response.text();
}

export async function apiPost<TReq, TRes>(path: string, body?: TReq): Promise<TRes> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return parseResponse<TRes>(response);
}

export async function apiPostForm<TRes>(path: string, body: FormData): Promise<TRes> {
  const response = await fetch(path, {
    method: "POST",
    credentials: "same-origin",
    body
  });
  return parseResponse<TRes>(response);
}

export async function apiPut<TReq, TRes>(path: string, body?: TReq): Promise<TRes> {
  const response = await fetch(path, {
    method: "PUT",
    credentials: "same-origin",
    headers: { "Content-Type": "application/json" },
    body: body === undefined ? undefined : JSON.stringify(body)
  });
  return parseResponse<TRes>(response);
}

export async function apiDelete<TRes>(path: string): Promise<TRes> {
  const response = await fetch(path, { method: "DELETE", credentials: "same-origin" });
  return parseResponse<TRes>(response);
}
