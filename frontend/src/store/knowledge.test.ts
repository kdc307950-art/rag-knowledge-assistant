import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { ApiError } from "../api/client";
import {
  getUploadTask,
  listDocuments,
  uploadDocuments,
} from "../api/knowledge";
import { getStats } from "../api/health";
import { ACTIVE_UPLOAD_TASKS_STORAGE_KEY, useKnowledge } from "./knowledge";

vi.mock("../api/knowledge", () => ({
  clearDocuments: vi.fn(),
  deleteDocument: vi.fn(),
  getUploadTask: vi.fn(),
  listDocuments: vi.fn(),
  uploadDocuments: vi.fn(),
}));

vi.mock("../api/health", () => ({
  getStats: vi.fn(),
}));

const getUploadTaskMock = vi.mocked(getUploadTask);
const listDocumentsMock = vi.mocked(listDocuments);
const uploadDocumentsMock = vi.mocked(uploadDocuments);
const getStatsMock = vi.mocked(getStats);

function createStorage() {
  const values = new Map<string, string>();
  return {
    getItem: (key: string) => values.get(key) ?? null,
    setItem: (key: string, value: string) => values.set(key, value),
    removeItem: (key: string) => values.delete(key),
  };
}

const stats = {
  healthy: true,
  vector_status: "ready",
  doc_count: 2,
  chunk_count: 4,
  generation: 3,
};

function resetStore() {
  useKnowledge.setState({
    documents: [],
    stats: null,
    tasks: {},
    loadingDocuments: true,
    uploading: false,
    error: "",
  });
}

beforeEach(() => {
  const session = createStorage();
  vi.stubGlobal("window", {
    dispatchEvent: vi.fn(),
    setTimeout,
  });
  vi.stubGlobal("sessionStorage", session);
  resetStore();
  listDocumentsMock.mockResolvedValue({ documents: ["manual.txt"] });
  getStatsMock.mockResolvedValue(stats);
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});

describe("knowledge upload workflow", () => {
  it("polls every task returned by a multi-file upload and refreshes the KB at terminal state", async () => {
    uploadDocumentsMock.mockResolvedValue({
      success: true,
      task_id: "task-a",
      async_tasks: ["task-a", "task-b"],
    });
    getUploadTaskMock.mockImplementation(async (taskId) => ({
      name: `${taskId}.txt`,
      status: "done",
      stage: "complete",
      progress: 100,
      message: "完成",
    }));

    await useKnowledge.getState().uploadFiles([
      new File(["a"], "a.txt"),
      new File(["b"], "b.txt"),
    ]);

    expect(getUploadTaskMock).toHaveBeenCalledTimes(2);
    expect(getUploadTaskMock).toHaveBeenCalledWith("task-a");
    expect(getUploadTaskMock).toHaveBeenCalledWith("task-b");
    expect(useKnowledge.getState().tasks).toMatchObject({
      "task-a": { status: "done", progress: 100 },
      "task-b": { status: "done", progress: 100 },
    });
    expect(listDocumentsMock).toHaveBeenCalledOnce();
    expect(getStatsMock).toHaveBeenCalledOnce();
    expect(useKnowledge.getState().documents).toEqual(["manual.txt"]);
    expect(useKnowledge.getState().stats).toEqual(stats);
    expect(useKnowledge.getState().uploading).toBe(false);
  });

  it("stops polling immediately when a task has expired with 404", async () => {
    uploadDocumentsMock.mockResolvedValue({
      success: true,
      task_id: "expired-task",
    });
    getUploadTaskMock.mockRejectedValue(new ApiError(404, "任务不存在或已过期"));

    await useKnowledge.getState().uploadFiles([new File(["a"], "a.txt")]);

    expect(getUploadTaskMock).toHaveBeenCalledOnce();
    expect(listDocumentsMock).toHaveBeenCalledOnce();
    expect(getStatsMock).toHaveBeenCalledOnce();
    expect(useKnowledge.getState().tasks["expired-task"]).toMatchObject({
      status: "error",
      stage: "stopped",
    });
    expect(useKnowledge.getState().uploading).toBe(false);
    expect(useKnowledge.getState().error).toBe("");
  });

  it("resumes a saved task after a tab refresh and clears its saved task id", async () => {
    sessionStorage.setItem(ACTIVE_UPLOAD_TASKS_STORAGE_KEY, JSON.stringify(["resume-task"]));
    getUploadTaskMock.mockResolvedValue({
      name: "resume.txt",
      status: "done",
      stage: "done",
      progress: 100,
      message: "完成",
    });

    await useKnowledge.getState().resumeUploadTasks();

    expect(getUploadTaskMock).toHaveBeenCalledWith("resume-task");
    expect(useKnowledge.getState().tasks["resume-task"]).toMatchObject({ status: "done" });
    expect(sessionStorage.getItem(ACTIVE_UPLOAD_TASKS_STORAGE_KEY)).toBeNull();
    expect(listDocumentsMock).toHaveBeenCalledOnce();
    expect(getStatsMock).toHaveBeenCalledOnce();
  });
});
