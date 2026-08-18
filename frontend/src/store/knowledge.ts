import { create } from "zustand";
import {
  clearDocuments,
  deleteDocument,
  getUploadTask,
  listDocuments,
  type UploadTask,
  type UploadTaskStatus,
  uploadDocuments,
  type UploadMetadata,
} from "../api/knowledge";
import { ApiError } from "../api/client";
import { getStats, type KbStats } from "../api/health";

const TERMINAL_STATUSES: ReadonlySet<UploadTaskStatus> = new Set(["done", "partial", "error"]);
const POLL_INTERVAL_MS = 1000;
const POLL_FAILURE_LIMIT = 4;
export const ACTIVE_UPLOAD_TASKS_STORAGE_KEY = "activeUploadTaskIds";

type StoreSet = (
  update: Partial<KnowledgeState> | ((state: KnowledgeState) => Partial<KnowledgeState>),
) => void;

interface KnowledgeState {
  documents: string[];
  stats: KbStats | null;
  tasks: Record<string, UploadTask>;
  loadingDocuments: boolean;
  uploading: boolean;
  error: string;
  refreshDocuments: () => Promise<void>;
  resumeUploadTasks: () => Promise<void>;
  uploadFiles: (files: File[], metadata?: UploadMetadata) => Promise<void>;
  removeDocument: (source: string) => Promise<void>;
  clearAll: () => Promise<void>;
  clearError: () => void;
}

function errorMessage(error: unknown) {
  return error instanceof Error ? error.message : "知识库操作失败，请稍后重试。";
}

function notifyKnowledgeChanged() {
  if (typeof window !== "undefined") {
    window.dispatchEvent(new CustomEvent("kb:changed"));
  }
}

function wait(milliseconds: number) {
  return new Promise<void>((resolve) => globalThis.setTimeout(resolve, milliseconds));
}

function readActiveTaskIds(): string[] {
  if (typeof sessionStorage === "undefined") return [];
  try {
    const stored = JSON.parse(sessionStorage.getItem(ACTIVE_UPLOAD_TASKS_STORAGE_KEY) ?? "[]");
    return Array.isArray(stored)
      ? stored.filter((taskId): taskId is string => typeof taskId === "string" && taskId.length > 0)
      : [];
  } catch {
    return [];
  }
}

function writeActiveTaskIds(taskIds: string[]) {
  if (typeof sessionStorage === "undefined") return;
  if (taskIds.length === 0) {
    sessionStorage.removeItem(ACTIVE_UPLOAD_TASKS_STORAGE_KEY);
    return;
  }
  sessionStorage.setItem(ACTIVE_UPLOAD_TASKS_STORAGE_KEY, JSON.stringify(taskIds));
}

function rememberTaskIds(taskIds: string[]) {
  writeActiveTaskIds(Array.from(new Set([...readActiveTaskIds(), ...taskIds])));
}

function forgetTaskIds(taskIds: string[]) {
  const completed = new Set(taskIds);
  writeActiveTaskIds(readActiveTaskIds().filter((taskId) => !completed.has(taskId)));
}

function stoppedTask(taskId: string, message: string): UploadTask {
  return {
    name: taskId,
    status: "error",
    stage: "stopped",
    progress: 0,
    message,
    fail_list: [message],
    updated_at: Date.now() / 1000,
  };
}

async function pollTask(taskId: string, set: StoreSet) {
  let failures = 0;
  while (true) {
    let task: UploadTask;
    try {
      task = await getUploadTask(taskId);
      failures = 0;
    } catch (error) {
      if (error instanceof ApiError && error.status === 401) {
        throw error;
      }
      if (error instanceof ApiError && error.status === 404) {
        const task = stoppedTask(taskId, "任务已过期或服务已重启，已停止轮询。正在刷新文档列表。");
        set((state) => ({ tasks: { ...state.tasks, [taskId]: task } }));
        return task;
      }
      failures += 1;
      if (failures > POLL_FAILURE_LIMIT) {
        const task = stoppedTask(taskId, "任务状态查询多次失败，已停止轮询。请检查服务状态。");
        set((state) => ({ tasks: { ...state.tasks, [taskId]: task } }));
        return task;
      }
      await wait(Math.min(5000, POLL_INTERVAL_MS * 2 ** failures));
      continue;
    }
    set((state) => ({ tasks: { ...state.tasks, [taskId]: task } }));
    if (TERMINAL_STATUSES.has(task.status)) {
      return task;
    }
    await wait(POLL_INTERVAL_MS);
  }
}

async function refreshKnowledgeSnapshot(set: StoreSet) {
  const [documents, stats] = await Promise.all([listDocuments(), getStats()]);
  set({ documents: documents.documents, stats, loadingDocuments: false });
}

async function watchTasks(taskIds: string[], set: StoreSet) {
  let completed = false;
  try {
    await Promise.all(taskIds.map((taskId) => pollTask(taskId, set)));
    completed = true;
  } finally {
    if (completed) {
      forgetTaskIds(taskIds);
    }
  }
}

export const useKnowledge = create<KnowledgeState>((set) => ({
  documents: [],
  stats: null,
  tasks: {},
  loadingDocuments: true,
  uploading: false,
  error: "",
  refreshDocuments: async () => {
    set({ loadingDocuments: true, error: "" });
    try {
      await refreshKnowledgeSnapshot(set);
    } catch (error) {
      set({ loadingDocuments: false, error: errorMessage(error) });
      throw error;
    }
  },
  resumeUploadTasks: async () => {
    const taskIds = readActiveTaskIds();
    if (taskIds.length === 0) return;
    set({ uploading: true, error: "" });
    try {
      await watchTasks(taskIds, set);
      await refreshKnowledgeSnapshot(set);
      notifyKnowledgeChanged();
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    } finally {
      set({ uploading: false });
    }
  },
  uploadFiles: async (files, metadata) => {
    set({ uploading: true, error: "" });
    try {
      const response = await uploadDocuments(files, metadata);
      const taskIds = Array.from(
        new Set(
          (response.async_tasks ?? []).concat(response.task_id ? [response.task_id] : []),
        ),
      );
      if (taskIds.length > 0) {
        rememberTaskIds(taskIds);
        await watchTasks(taskIds, set);
      }
      await refreshKnowledgeSnapshot(set);
      notifyKnowledgeChanged();
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    } finally {
      set({ uploading: false });
    }
  },
  removeDocument: async (source) => {
    set({ error: "" });
    try {
      await deleteDocument(source);
      await refreshKnowledgeSnapshot(set);
      notifyKnowledgeChanged();
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    }
  },
  clearAll: async () => {
    set({ error: "" });
    try {
      await clearDocuments();
      await refreshKnowledgeSnapshot(set);
      notifyKnowledgeChanged();
    } catch (error) {
      set({ error: errorMessage(error) });
      throw error;
    }
  },
  clearError: () => set({ error: "" }),
}));
