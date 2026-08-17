import { request } from "./client";

export interface Health {
  ok: boolean;
  vector_status: string;
}

export interface KbStats {
  healthy: boolean;
  vector_status: string;
  doc_count: number;
  chunk_count: number;
  generation: number | null;
}

export const getHealth = () => request<Health>("/health");
export const getStats = () => request<KbStats>("/kb/stats");
