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

export interface DiagnosticCheck {
  ok: boolean;
  status?: string;
  state?: string;
  detail?: string;
  model?: string;
  generation?: number | null;
}

export interface Diagnostics {
  ok: boolean;
  checked_at: string;
  checks: {
    vector_store: DiagnosticCheck;
    manifest: DiagnosticCheck;
    embedding: DiagnosticCheck;
    reranker: DiagnosticCheck;
    llm: DiagnosticCheck & { network_verified: boolean };
  };
  kb: {
    document_count: number;
    chunk_count: number;
    generation: number | null;
  };
  errors: {
    count: number;
    truncated: boolean;
    recent: Array<{
      timestamp: string;
      level: string;
      logger: string;
      message: string;
    }>;
  };
  llm_cost?: {
    estimated: boolean;
    price_table: {
      configured: boolean;
      entries: number;
    };
    totals: Array<{
      currency: string;
      confidence: "exact" | "estimated";
      mode: string;
      amount: number;
    }>;
  };
}

export const getHealth = () => request<Health>("/health");
export const getStats = () => request<KbStats>("/kb/stats");
export const getDiagnostics = () => request<Diagnostics>("/diagnostics");
