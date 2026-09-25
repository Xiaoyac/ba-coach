import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";
import type { SandboxModule } from "@/lib/adminSandbox";

export interface TestCaseInput {
  case_code: string; module: SandboxModule; user_type: string; scenario: string;
  user_input: string; expected_behavior: string; extra_columns: Record<string, string>;
}
export interface TestCase extends TestCaseInput { id: string; revision: number; updated_at: string }
export interface TestReview { id: string; reviewer_id: number; verdict: string; notes: string; created_at: string }
export interface ExecutionTimelineEntry {
  stage: string; start_ms?: number | null; duration_ms?: number | null; status?: string;
  after_display?: boolean; retry_count?: number; reason?: string;
}
export interface DiagnosticSnapshot {
  status?: string; error?: unknown; error_code?: string; [key: string]: unknown;
}
export interface TestTelemetry {
  answer_validator?: {
    status: string; version?: string; duration_ms?: number; llm_calls?: number;
    findings?: {code: string; severity: string}[];
    progression_held?: boolean; original_reply?: string; replacement_reply?: string;
    display_source?: string; replacement_source?: string; reason?: string;
  };
  retrieval?: { gate?: { reason: string; retrieve: boolean }; returned?: number; total_duration_ms?: number };
  prompt_version?: string;
  execution_timeline?: ExecutionTimelineEntry[];
  prompt_sources?: {source: string; version?: string | null}[];
  time_to_first_visible_content_ms?: number | null;
  first_visible_measurement?: string;
  main_input?: { system: string; messages: { role: string; content: string }[] };
  pre_reply_extraction?: DiagnosticSnapshot;
  reply_trace?: { raw_model_reply: string; normalized_reply: string; visible_reply: string; display_source: string };
  router_pre_reply?: {
    input_sources?: string[]; selected_module?: string | null; database_module?: string | null;
    status?: string; retry_count?: number; reason?: string;
  };
  extraction?: DiagnosticSnapshot;
  database_write?: DiagnosticSnapshot;
}
export interface TestRun {
  id: string; case_id: string; parent_run_id: string | null; status: string; latest_verdict?: string;
  case_snapshot: TestCase; provider: string; model: string | null; input_text: string; reply: string;
  transcript: { role: string; content: string }[];
  metrics: { duration_ms?: number; usage?: Record<string, number>; risk_flagged?: boolean;
    telemetry?: TestTelemetry;
    retrieved?: { id: string; source: string; score: number }[] };
  error_code: string | null; created_at: string; finished_at: string | null; reviews?: TestReview[];
}
export interface ImportPreview { sheets: string[]; selected_sheet: string; cases: TestCaseInput[]; errors: {row: number; message: string}[] }
export type TestProvider = "claude" | "deepseek" | "doubao";

const root = `${API_BASE}/api/admin/evaluations`;
export async function evaluationRequest<T>(path: string, body?: unknown, method = "GET", signal?: AbortSignal): Promise<T> {
  const res = await fetch(root + path, { method, signal, headers: apiHeaders({ json: body !== undefined }),
    body: body === undefined ? undefined : JSON.stringify(body) });
  if (!res.ok) {
    const data = await res.json().catch(() => null);
    const detail = typeof data?.detail === "string" ? data.detail : `操作失败（${res.status}），请检查输入或联系管理员`;
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export async function downloadEvaluation(path: "cases.csv" | "results.csv") {
  const response = await fetch(`${root}/${path}`, { headers: apiHeaders() });
  if (!response.ok) throw new Error(`导出失败（${response.status}）`);
  const url = URL.createObjectURL(await response.blob());
  const link = document.createElement("a");
  link.href = url; link.download = path === "cases.csv" ? "评测集.csv" : "测试记录.csv";
  link.click(); setTimeout(() => URL.revokeObjectURL(url), 1000);
}

export async function previewEvaluation(file: File, sheet?: string): Promise<ImportPreview> {
  if (file.size > 5_000_000) throw new Error("文件不能超过 5 MB");
  const content = await new Promise<string>((resolve, reject) => {
    const reader = new FileReader();
    reader.onload = () => resolve(String(reader.result).split(",")[1]);
    reader.onerror = () => reject(new Error("读取文件失败"));
    reader.readAsDataURL(file);
  });
  return evaluationRequest("/import/preview", { filename: file.name, content_base64: content, sheet }, "POST");
}
