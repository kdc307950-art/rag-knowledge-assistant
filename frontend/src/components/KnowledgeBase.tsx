import { useState } from "react";
import { useKnowledge } from "../store/knowledge";

interface KnowledgeBaseProps {
  disabled?: boolean;
}

export default function KnowledgeBase({ disabled = false }: KnowledgeBaseProps) {
  const documents = useKnowledge((state) => state.documents);
  const stats = useKnowledge((state) => state.stats);
  const loading = useKnowledge((state) => state.loadingDocuments);
  const removeDocument = useKnowledge((state) => state.removeDocument);
  const clearAll = useKnowledge((state) => state.clearAll);
  const [busySource, setBusySource] = useState("");
  const [clearing, setClearing] = useState(false);

  const remove = async (source: string) => {
    setBusySource(source);
    try {
      await removeDocument(source);
    } finally {
      setBusySource("");
    }
  };

  const clear = async () => {
    if (disabled || documents.length === 0 || !window.confirm("确认清空知识库中的全部文档吗？")) return;
    setClearing(true);
    try {
      await clearAll();
    } finally {
      setClearing(false);
    }
  };

  return (
    <section aria-labelledby="knowledge-base-title">
      <div className="mb-2 flex items-center justify-between gap-2">
        <h2 id="knowledge-base-title" className="text-xs font-semibold uppercase tracking-wide text-gray-500">
          知识库文档
        </h2>
        <button
          type="button"
          disabled={disabled || clearing || busySource !== "" || documents.length === 0}
          onClick={() => void clear()}
          className="text-xs text-red-700 hover:underline disabled:cursor-not-allowed disabled:text-gray-400"
        >
          清空
        </button>
      </div>
      {loading && <p className="text-xs text-gray-500">正在读取文档列表...</p>}
      {!loading && stats && !stats.healthy && (
        <p className="text-xs leading-5 text-red-700">知识库不可用：{stats.vector_status}</p>
      )}
      {!loading && (!stats || stats.healthy) && documents.length === 0 && (
        <p className="text-xs text-gray-500">暂无文档</p>
      )}
      <ul className="space-y-1">
        {documents.map((source) => (
          <li key={source} className="flex items-start gap-2 border-b border-gray-100 py-2 text-sm">
            <span className="min-w-0 flex-1 break-words text-gray-700">{source}</span>
            <button
              type="button"
              disabled={disabled || busySource !== "" || clearing}
              onClick={() => void remove(source)}
              className="shrink-0 text-xs text-red-700 hover:underline disabled:cursor-not-allowed disabled:text-gray-400"
            >
              {busySource === source ? "删除中" : "删除"}
            </button>
          </li>
        ))}
      </ul>
    </section>
  );
}
