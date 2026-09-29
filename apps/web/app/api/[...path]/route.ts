import { NextRequest } from "next/server";

async function proxy(request: NextRequest, context: { params: Promise<{path: string[]}> }) {
  const {path} = await context.params;
  const base = process.env.BACKEND_URL || "http://127.0.0.1:8000";
  const url = `${base}/api/${path.map(encodeURIComponent).join("/")}${request.nextUrl.search}`;
  const headers = new Headers({"Content-Type": "application/json"});
  for (const name of ["authorization", "last-event-id"]) {
    const value = request.headers.get(name);
    if (value) headers.set(name, value);
  }
  try {
    const response = await fetch(url, {method: request.method, headers,
      body: request.method === "GET" ? undefined : await request.text(),
      cache: "no-store", signal: request.signal});
    return new Response(response.body, {status: response.status, headers: {
      "Content-Type": response.headers.get("content-type") || "application/json",
      "Cache-Control": "no-store", "X-Accel-Buffering": "no"}});
  } catch {
    return Response.json({detail: "后端服务暂不可用，请稍后重试"}, {status: 502});
  }
}
export {proxy as GET, proxy as POST, proxy as PUT};
