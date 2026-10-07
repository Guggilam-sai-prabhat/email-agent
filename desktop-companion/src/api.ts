const BASE_URL = "http://127.0.0.1:8000";

export interface BriefItem {
  message_id: string;
  subject: string;
  summary: string;
  priority: "low" | "medium" | "high" | "urgent" | string;
  category: string | null;
  explicit_deadline: string | null;
  is_overdue: boolean;
}

export interface DailyBrief {
  generated_at: string;
  urgent: BriefItem[];
  deadlines: BriefItem[];
  needs_reply: BriefItem[];
  other_action_items: BriefItem[];
  informational: BriefItem[];
  suggested_order: BriefItem[];
}

export interface AgentStatus {
  model_available: boolean;
  model_name: string;
  gmail_authorized: boolean;
  run_in_progress: boolean;
  last_successful_run_at: string | null;
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`);
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

export const fetchDailyBrief = () => getJson<DailyBrief>("/api/v1/daily-brief");
export const fetchStatus = () => getJson<AgentStatus>("/api/v1/status");

export async function startRun(): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/v1/runs`, { method: "POST" });
  if (!res.ok && res.status !== 409) throw new Error(`start run failed: ${res.status}`);
}
