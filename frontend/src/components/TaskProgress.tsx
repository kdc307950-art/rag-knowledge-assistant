import type { UploadTask } from "../api/knowledge";

interface TaskProgressProps {
  tasks: Record<string, UploadTask>;
}

const STATUS_LABELS: Record<UploadTask["status"], string> = {
  pending: "排队中",
  processing: "处理中",
  done: "完成",
  partial: "部分完成",
  error: "失败",
};

export default function TaskProgress({ tasks }: TaskProgressProps) {
  const entries = Object.entries(tasks).slice(-3).reverse();
  if (entries.length === 0) return null;

  return (
    <section aria-labelledby="task-progress-title">
      <h2 id="task-progress-title" className="mb-2 text-xs font-semibold uppercase tracking-wide text-gray-500">
        上传任务
      </h2>
      <div className="space-y-3">
        {entries.map(([taskId, task]) => (
          <div key={taskId} className="border border-gray-200 bg-white px-3 py-2">
            <div className="flex items-start justify-between gap-2 text-xs">
              <span className="min-w-0 break-words font-medium text-gray-800">{task.name}</span>
              <span className="shrink-0 text-gray-500">{STATUS_LABELS[task.status] ?? task.status}</span>
            </div>
            <div
              className="mt-2 h-1.5 bg-gray-100"
              role="progressbar"
              aria-label={`${task.progress}%`}
              aria-valuemin={0}
              aria-valuemax={100}
              aria-valuenow={task.progress}
            >
              <div
                className={"h-full " + (task.status === "error" ? "bg-red-500" : "bg-blue-600")}
                style={{ width: `${Math.max(0, Math.min(100, task.progress))}%` }}
              />
            </div>
            <p className="mt-1 text-xs leading-5 text-gray-500">{task.message ?? `${task.progress}%`}</p>
            {task.fail_list && task.fail_list.length > 0 && (
              <details className="mt-1 text-xs text-red-700">
                <summary className="cursor-pointer">查看失败项</summary>
                <ul className="mt-1 list-disc space-y-1 pl-4">
                  {task.fail_list.map((failure, index) => <li key={`${taskId}-${index}`}>{failure}</li>)}
                </ul>
              </details>
            )}
            {task.cleanup_pending && (
              <p className="mt-1 text-xs text-amber-700">暂存文件尚未清理，系统会在后续任务中重试。</p>
            )}
          </div>
        ))}
      </div>
    </section>
  );
}
