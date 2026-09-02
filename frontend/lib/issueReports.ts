import { API_BASE } from "@/lib/api";
import { apiHeaders } from "@/lib/http";

export interface IssueReportInput {
  description: string;
  screenshot_data_url: string | null;
  page_url: string;
  session_id: string | null;
  last_error: string | null;
  user_agent: string;
  viewport_width: number;
  viewport_height: number;
  client_online: boolean;
}

export interface IssueReportCreated {
  id: number;
  created_at: string;
}

export interface AdminIssueReportItem {
  id: number;
  username: string;
  display_name: string | null;
  description: string;
  status: "open" | "resolved";
  has_screenshot: boolean;
  screenshot_mime: string | null;
  page_url: string;
  session_id: string | null;
  last_error: string | null;
  user_agent: string;
  viewport_width: number | null;
  viewport_height: number | null;
  client_online: boolean | null;
  created_at: string;
  resolved_at: string | null;
}

async function detail(response: Response, fallback: string): Promise<string> {
  const body = await response.json().catch(() => null);
  return typeof body?.detail === "string" ? body.detail : fallback;
}

export async function submitIssueReport(
  input: IssueReportInput,
): Promise<IssueReportCreated> {
  const response = await fetch(`${API_BASE}/api/issue-reports`, {
    method: "POST",
    headers: apiHeaders({ json: true }),
    body: JSON.stringify(input),
  });
  if (!response.ok) {
    throw new Error(await detail(response, "反馈提交失败，请稍后再试"));
  }
  return response.json() as Promise<IssueReportCreated>;
}

export async function fetchAdminIssueReports(
  status: "open" | "resolved" | "all" = "open",
  signal?: AbortSignal,
): Promise<AdminIssueReportItem[]> {
  const params = new URLSearchParams({ limit: "100" });
  if (status !== "all") params.set("status", status);
  const response = await fetch(
    `${API_BASE}/api/admin/issue-reports?${params.toString()}`,
    { headers: apiHeaders(), signal },
  );
  if (!response.ok) {
    throw new Error(await detail(response, "无法读取问题反馈"));
  }
  const body = (await response.json()) as { reports: AdminIssueReportItem[] };
  return body.reports;
}

export async function fetchIssueScreenshot(reportId: number): Promise<Blob> {
  const response = await fetch(
    `${API_BASE}/api/admin/issue-reports/${reportId}/screenshot`,
    { headers: apiHeaders() },
  );
  if (!response.ok) {
    throw new Error(await detail(response, "无法读取问题截图"));
  }
  return response.blob();
}

export async function updateIssueReportStatus(
  reportId: number,
  status: "open" | "resolved",
): Promise<AdminIssueReportItem> {
  const response = await fetch(
    `${API_BASE}/api/admin/issue-reports/${reportId}/status`,
    {
      method: "PATCH",
      headers: apiHeaders({ json: true }),
      body: JSON.stringify({ status }),
    },
  );
  if (!response.ok) {
    throw new Error(await detail(response, "更新反馈状态失败"));
  }
  return response.json() as Promise<AdminIssueReportItem>;
}
