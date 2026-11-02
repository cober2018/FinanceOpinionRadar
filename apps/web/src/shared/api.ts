// 拆分自原 App.tsx（统一架构 tasks 3.1）；可见行为与调用零改动。

// 全局在途请求计数：任何非静默 api() 调用都会点亮右上角「处理中」转圈指示
let apiInFlight = 0
function emitApiBusy() {
  window.dispatchEvent(new CustomEvent('api-busy', { detail: apiInFlight }))
}

async function api<T>(path: string, init?: RequestInit, opts?: { silent?: boolean }): Promise<T> {
  if (!opts?.silent) {
    apiInFlight += 1
    emitApiBusy()
  }
  try {
    const resp = await fetch(`/api/v1${path}`, {
      headers: { 'content-type': 'application/json' },
      ...init,
    })
    if (!resp.ok) {
      const body = await resp.text()
      let detail = body.slice(0, 200)
      try {
        detail = JSON.parse(body).detail ?? detail
      } catch {
        /* 原样使用 */
      }
      throw new Error(detail)
    }
    return resp.json() as Promise<T>
  } finally {
    if (!opts?.silent) {
      apiInFlight -= 1
      emitApiBusy()
    }
  }
}


export { api, emitApiBusy }
