import { invoke } from "@tauri-apps/api/core";
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
  truncated: boolean;
  unscanned_count: number;

export type NagLevel = "quiet" | "noticeable" | "persistent";

export interface OpenItem {
  message_id: string;
  subject: string;
  sender_address: string;
  received_at: string;
  age_hours: number;
  nag_level: NagLevel;
}
  gmail_url: string | null;

// The shared secret is read from the backend's key file by the Rust side (read_api_key);
// a manual "api_key" in companion-settings.json still takes precedence.
async function authHeaders(): Promise<HeadersInit> {
  const store = await load("companion-settings.json");
  const key = (await store.get<string>("api_key")) ?? (await invoke<string | null>("read_api_key"));
  if (!key) throw new Error("Backend not started yet (no API key file found). Start the Mail Agent backend.");
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
