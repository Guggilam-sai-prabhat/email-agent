import { load } from "@tauri-apps/plugin-store";

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

export type NagLevel = "quiet" | "noticeable" | "persistent";

export interface OpenItem {
  message_id: string;
  subject: string;
  sender_address: string;
  received_at: string;
  age_hours: number;
  nag_level: NagLevel;
}

// The shared secret lives in companion-settings.json under "api_key" (copy it
// from the backend's .api_key file once).
async function authHeaders(): Promise<HeadersInit> {
  const store = await load("companion-settings.json");
  const key = await store.get<string>("api_key");
  if (!key) throw new Error('Set "api_key" in companion-settings.json (see backend .api_key)');
  return { "X-API-Key": key };
}

async function getJson<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE_URL}${path}`, { headers: await authHeaders() });
  if (!res.ok) throw new Error(`${path} failed: ${res.status}`);
  return res.json();
}

export const fetchDailyBrief = () => getJson<DailyBrief>("/api/v1/daily-brief");
export const fetchOpenItems = () => getJson<OpenItem[]>("/api/v1/open-items");
export const fetchStatus = () => getJson<AgentStatus>("/api/v1/status");

export async function startRun(): Promise<void> {
  const res = await fetch(`${BASE_URL}/api/v1/runs`, { method: "POST", headers: await authHeaders() });
  if (!res.ok && res.status !== 409) throw new Error(`start run failed: ${res.status}`);
}
