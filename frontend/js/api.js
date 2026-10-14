// Fetch wrapper for the backend. Errors raise ApiError and show a toast unless {quiet: true}.
import { toast } from "./components/toast.js";

const BASE = "/api";

export class ApiError extends Error {
  constructor(status, message, body) {
    super(message);
    this.status = status;
    this.body = body;
  }
}

/** FastAPI errors: {detail: "text"} or {detail: [{loc, msg}, ...]} (pydantic validation). */
export function errorMessage(body, status) {
  const detail = body && body.detail;
  if (typeof detail === "string") return detail;
  if (Array.isArray(detail)) {
    return detail.map((d) => {
      const loc = (d.loc || []).filter((p) => p !== "body" && p !== "query").join(".");
      return loc ? `${loc}: ${d.msg}` : d.msg;
    }).join("\n");
  }
  return `Request failed (${status})`;
}

async function request(method, path, { json, form, quiet = false } = {}) {
  const opts = { method, headers: {} };
  if (json !== undefined) {
    opts.headers["Content-Type"] = "application/json";
    opts.body = JSON.stringify(json);
  } else if (form) {
    opts.body = form;
  }
  let res;
  try {
    res = await fetch(BASE + path, opts);
  } catch (err) {
    const e = new ApiError(0, "Cannot reach the server. Is the backend running?");
    if (!quiet) toast(e.message, "error");
    throw e;
  }
  const type = res.headers.get("content-type") || "";
  const body = type.includes("application/json") ? await res.json() : await res.text();
  if (!res.ok) {
    const e = new ApiError(res.status, errorMessage(body, res.status), body);
    if (!quiet) toast(e.message, "error");
    throw e;
  }
  return body;
}

export const api = {
  get: (path, opts) => request("GET", path, opts),
  post: (path, json, opts = {}) => request("POST", path, { ...opts, json }),
  upload: (path, form, opts = {}) => request("POST", path, { ...opts, form }),
};
