import { useEffect, useState } from "react";
import { getHealth, getStats } from "../api/health";
import { useAuth } from "../store/auth";

export default function StatusBar() {
  const logout = useAuth((s) => s.logout);
  const [health, setHealth] = useState("检查中...");
  const [stats, setStats] = useState("");
  const [tick, setTick] = useState(0);

  useEffect(() => {
    let alive = true;
    getHealth()
      .then((h) => {
        if (alive) setHealth(h.ok ? "后端就绪（" + h.vector_status + "）" : "后端异常：" + h.vector_status);
      })
      .catch(() => alive && setHealth("后端不可用"));
    getStats()
      .then((s) => {
        if (alive) setStats("文档 " + s.doc_count + " · 分块 " + s.chunk_count + " · 代际 " + (s.generation ?? "-"));
      })
      .catch(() => {});
    return () => {
      alive = false;
    };
  }, [tick]);

  return (
    <header className="flex items-center justify-between border-b px-4 py-2 bg-white">
      <div className="flex items-center gap-3">
        <span className="font-semibold">企业知识库助手</span>
        <span className="text-sm text-gray-500">{health}</span>
        {stats && <span className="text-sm text-gray-400">{stats}</span>}
      </div>
      <div>
        <button
          onClick={() => setTick((n) => n + 1)}
          className="text-sm text-blue-600 mr-4 hover:underline"
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
