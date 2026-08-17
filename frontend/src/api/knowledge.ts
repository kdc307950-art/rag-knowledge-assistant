import { request } from "./client";

export type UploadTaskStatus = "pending" | "processing" | "done" | "partial" | "error";

export interface UploadTask {
  name: string;
  status: UploadTaskStatus;
  stage: string;
  progress: number;
  file_count?: number;
  total_bytes?: number;
  message?: string;
  success_count?: number;
  added_count?: number;
  skipped_count?: number;
  fail_list?: string[];
  cleanup_pending?: boolean;
  doc_count?: number | null;
  chunk_count?: number | null;
  updated_at?: number;
}

export interface UploadResponse {
  success: boolean;
  task_id?: string | null;
  async_tasks?: string[];
  message?: string;
  error?: string;
}

export interface DocumentsResponse {
  documents: string[];
}

export function listDocuments() {
  return request<DocumentsResponse>("/kb/documents");
}

export function uploadDocuments(files: File[]) {
  const body = new FormData();
  for (const file of files) {
    body.append("files", file, file.name);
  }
  return request<UploadResponse>("/upload", { method: "POST", body });
}

export function getUploadTask(taskId: string) {
  return request<UploadTask>(`/tasks/${encodeURIComponent(taskId)}`);
}

export function deleteDocument(source: string) {
  return request<{ ok: boolean }>(`/kb/documents/${encodeURIComponent(source)}`, {
    method: "DELETE",
  });
}

export function clearDocuments() {
  return request<{ ok: boolean }>("/kb/clear", { method: "DELETE" });
}
