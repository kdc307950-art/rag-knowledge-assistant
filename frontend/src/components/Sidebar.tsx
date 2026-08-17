import { useEffect } from "react";
import { useKnowledge } from "../store/knowledge";
import { useSettings } from "../store/settings";
import KnowledgeBase from "./KnowledgeBase";
import TaskProgress from "./TaskProgress";
import UploadZone from "./UploadZone";

export default function Sidebar() {
  const tasks = useKnowledge((state) => state.tasks);
  const uploading = useKnowledge((state) => state.uploading);
  const error = useKnowledge((state) => state.error);
  const refreshDocuments = useKnowledge((state) => state.refreshDocuments);
  const resumeUploadTasks = useKnowledge((state) => state.resumeUploadTasks);
  const uploadFiles = useKnowledge((state) => state.uploadFiles);
  const showDebug = useSettings((state) => state.showDebug);
  const setShowDebug = useSettings((state) => state.setShowDebug);

  useEffect(() => {
    void refreshDocuments().catch(() => undefined);
  }, [refreshDocuments]);

  useEffect(() => {
    void resumeUploadTasks().catch(() => undefined);
  }, [resumeUploadTasks]);

  return (
    <aside className="max-h-[42vh] shrink-0 overflow-y-auto border-b bg-white px-4 py-4 md:max-h-none md:w-72 md:border-b-0 md:border-r">
      <div className="space-y-5">
        <div>
          <h1 className="text-sm font-semibold text-gray-900">资料管理</h1>
          <p className="mt-1 text-xs leading-5 text-gray-500">上传后等待任务完成，再进行问答。</p>
        </div>
        <UploadZone disabled={uploading} onUpload={uploadFiles} />
        {uploading && <p className="text-xs text-blue-700">后台正在处理上传任务...</p>}
        {error && <p className="border border-red-200 bg-red-50 px-3 py-2 text-xs leading-5 text-red-700">{error}</p>}
        <TaskProgress tasks={tasks} />
        <KnowledgeBase disabled={uploading} />
        <label className="flex items-center gap-2 border-t pt-4 text-xs text-gray-600">
          <input
            type="checkbox"
            checked={showDebug}
            onChange={(event) => setShowDebug(event.target.checked)}
            className="h-4 w-4 accent-blue-700"
          />
          显示回答依据
        </label>
      </div>
    </aside>
  );
}
