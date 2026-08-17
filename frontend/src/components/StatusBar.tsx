import { useEffect, useState } from "react";
import { getDiagnostics, type Diagnostics } from "../api/health";
import { useAuth } from "../store/auth";

function modelLabel(state?: string) {
  if (state === "loaded") return "已加载";
  if (state === "available") return "可用";
  return "异常";
}

export default function StatusBar() {
  const logout = useAuth((s) => s.logout);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [status, setStatus] = useState("检查中...");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    setStatus("检查中...");
    getDiagnostics()
      .then((report) => {
        if (!alive) return;
        setDiagnostics(report);
        setStatus(report.ok ? "系统就绪" : "系统需要检查");
      })
      .catch(() => {
        if (!alive) return;
        setDiagnostics(null);
        setStatus("后端不可用");
      });
    return () => {
      alive = false;
    };
  }, [tick]);

  return (
    <header className="relative flex flex-wrap items-center justify-between gap-3 border-b bg-white px-4 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold">企业知识库助手</span>
        <span className={"text-sm " + (diagnostics?.ok ? "text-green-700" : "text-amber-700")}>{status}</span>
        {diagnostics && (
          <span className="text-sm text-gray-500">
            文档 {diagnostics.kb.document_count} · 分块 {diagnostics.kb.chunk_count} · 代际 {diagnostics.kb.generation ?? "-"}
          </span>
        )}
        {diagnostics && (
          <span className="text-sm text-gray-400">
            嵌入 {modelLabel(diagnostics.checks.embedding.state)} · 重排 {modelLabel(diagnostics.checks.reranker.state)}
          </span>
        )}
      </div>
      <div className="flex items-center gap-3">
        {diagnostics && diagnostics.errors.count > 0 && (
          <details className="relative">
            <summary className="cursor-pointer list-none text-sm text-red-700">
              {diagnostics.errors.count}{diagnostics.errors.truncated ? "+" : ""} 条错误
            </summary>
            <div className="absolute right-0 top-7 z-20 w-[min(24rem,calc(100vw-2rem))] border bg-white p-3 shadow-lg">
              <p className="mb-2 text-sm font-medium text-gray-800">最近 24 小时</p>
              <div className="space-y-2">
                {diagnostics.errors.recent.map((item) => (
                  <div key={item.timestamp + item.message} className="border-t pt-2 first:border-t-0 first:pt-0">
                    <p className="text-xs text-gray-400">{new Date(item.timestamp).toLocaleString()}</p>
                    <p className="break-words text-sm text-gray-700">{item.message}</p>
                  </div>
                ))}
              </div>
            </div>
          </details>
        )}
        <button
          onClick={() => setTick((n) => n + 1)}
          className="text-sm text-blue-600 hover:underline"
        >
          刷新
        </button>
        <button onClick={logout} className="text-sm text-gray-500 hover:underline">
          退出
        </button>
      </div>
    </header>
  );
}
