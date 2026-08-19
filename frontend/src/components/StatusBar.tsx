import { useEffect, useState } from "react";
import { getDiagnostics, getStats, type Diagnostics, type KbStats } from "../api/health";
import { friendlyApiError } from "../api/client";
import { useAuth } from "../store/auth";

function modelLabel(state?: string) {
  if (state === "loaded") return "已加载";
  if (state === "available") return "可用";
  return "异常";
}

function costLabel(diagnostics: Diagnostics) {
  const totals = diagnostics.llm_cost?.totals ?? [];
  if (!totals.length) return diagnostics.llm_cost?.price_table.configured === false ? "成本估算未配置" : "暂无成本数据";
  return totals
    .map((item) => `${item.currency} ${item.amount.toFixed(4)}${item.confidence === "estimated" ? "（均价估算）" : ""}`)
    .join(" · ");
}

export default function StatusBar() {
  const logout = useAuth((s) => s.logout);
  const user = useAuth((s) => s.user);
  const [diagnostics, setDiagnostics] = useState<Diagnostics | null>(null);
  const [stats, setStats] = useState<KbStats | null>(null);
  const [status, setStatus] = useState("检查中...");
  const [tick, setTick] = useState(0);
  const isAdmin = user?.roles.includes("admin") ?? false;

  useEffect(() => {
    let alive = true;
    setStatus("检查中...");
    setDiagnostics(null);
    setStats(null);
    const load = isAdmin
      ? getDiagnostics().then((report) => {
        if (!alive) return;
        setDiagnostics(report);
        setStatus(report.ok ? "系统就绪" : "系统需要检查");
      })
      : getStats().then((snapshot) => {
        if (!alive) return;
        setStats(snapshot);
        setStatus(snapshot.healthy ? "系统就绪" : "知识库需要检查");
      });
    load.catch((error: unknown) => {
      if (!alive) return;
      setDiagnostics(null);
      setStats(null);
      setStatus(friendlyApiError(error, "后端不可用"));
    });
    return () => {
      alive = false;
    };
  }, [isAdmin, tick]);

  useEffect(() => {
    const onKnowledgeChanged = () => setTick((value) => value + 1);
    window.addEventListener("kb:changed", onKnowledgeChanged);
    return () => window.removeEventListener("kb:changed", onKnowledgeChanged);
  }, []);

  const documentCount = diagnostics?.kb.document_count ?? stats?.doc_count;
  const chunkCount = diagnostics?.kb.chunk_count ?? stats?.chunk_count;
  const generation = diagnostics?.kb.generation ?? stats?.generation;
  const isHealthy = diagnostics?.ok ?? stats?.healthy;

  return (
    <header className="relative flex flex-wrap items-center justify-between gap-3 border-b bg-white px-4 py-2">
      <div className="flex min-w-0 flex-wrap items-center gap-x-3 gap-y-1">
        <span className="font-semibold">企业知识库助手</span>
        <span className={"text-sm " + (isHealthy ? "text-green-700" : "text-amber-700")}>{status}</span>
        {documentCount !== undefined && (
          <span className="text-sm text-gray-500">
            文档 {documentCount} · 分块 {chunkCount ?? 0} · 代际 {generation ?? "-"}
          </span>
        )}
        {diagnostics && (
          <span className="text-sm text-gray-400">
            嵌入 {modelLabel(diagnostics.checks.embedding.state)} · 重排 {modelLabel(diagnostics.checks.reranker.state)}
          </span>
        )}
        {diagnostics && <span className="text-sm text-gray-400">LLM {costLabel(diagnostics)}</span>}
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
