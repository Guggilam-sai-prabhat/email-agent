import { useEffect, useRef, useState } from "react";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";
import { sendNotification, isPermissionGranted, requestPermission } from "@tauri-apps/plugin-notification";
import { load } from "@tauri-apps/plugin-store";
import { fetchDailyBrief, fetchStatus, startRun, type DailyBrief, type AgentStatus } from "./api";
import orbIcon from "./assets/man.png";
import "./App.css";

const INTERVAL_MS = 30 * 60 * 1000;

function countActionItems(brief: DailyBrief): number {
  return brief.urgent.length + brief.deadlines.length + brief.needs_reply.length + brief.other_action_items.length;
}

function App() {
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function refresh(notify: boolean) {
    setChecking(true);
    try {
      const [s, b] = await Promise.all([fetchStatus(), fetchDailyBrief()]);
      setStatus(s);
      setBrief(b);
      setError(null);
      if (notify) {
        await getCurrentWindow().show();
        const count = countActionItems(b);
        const granted = (await isPermissionGranted()) || (await requestPermission()) === "granted";
        if (granted) {
          sendNotification({
            title: "Mail Agent Companion",
            body: count > 0 ? `${count} items need your attention` : "Inbox is clear",
          });
        }
      }
    } catch (e) {
      setError(e instanceof Error ? e.message : String(e));
    } finally {
      setChecking(false);
    }
  }

  const timerRef = useRef<ReturnType<typeof setInterval> | null>(null);

  useEffect(() => {
    refresh(false);
    timerRef.current = setInterval(() => refresh(true), INTERVAL_MS);

    (async () => {
      const store = await load("companion-settings.json");
      await store.set("interval_minutes", 30);
      await store.save();
    })();

    return () => {
      if (timerRef.current) clearInterval(timerRef.current);
    };
  }, []);

  async function setPanel(open: boolean) {
    setPanelOpen(open);
    await getCurrentWindow().setSize(new LogicalSize(open ? 340 : 80, open ? 460 : 80));
  }

  async function togglePanel() {
    await setPanel(!panelOpen);
  }

  useEffect(() => {
    if (!panelOpen) return;
    const timeout = setTimeout(() => setPanel(false), 5000);
    return () => clearTimeout(timeout);
  }, [panelOpen]);

  // The orb itself auto-hides after a glance; it reappears on the next
  // scheduled refresh (or via the tray's "Show") rather than staying pinned
  // on screen between checks.
  useEffect(() => {
    if (panelOpen) return;
    const timeout = setTimeout(() => getCurrentWindow().hide(), 30000);
    return () => clearTimeout(timeout);
  }, [panelOpen, brief]);

  async function hideWidget() {
    await getCurrentWindow().hide();
  }

  const actionCount = brief ? countActionItems(brief) : 0;
  const widgetMood = error ? "error" : actionCount > 0 ? "alert" : "calm";

  return (
    <div className="companion">
      <button
        className={`orb ${widgetMood} ${checking ? "spinning" : ""}`}
        onClick={togglePanel}
        title={error ?? (status ? `Model: ${status.model_available ? "ready" : "unavailable"}` : "Loading...")}
        data-tauri-drag-region
      >
        <img src={orbIcon} alt="" className="orb-icon" />
        {actionCount > 0 && <span className="badge">{actionCount}</span>}
      </button>

      {panelOpen && (
        <div className="panel">
          <div className="panel-header" data-tauri-drag-region>
            <span>Daily Brief</span>
            <div className="panel-actions">
              <button onClick={() => refresh(false)} disabled={checking}>
                ⟳
              </button>
              <button onClick={() => startRun()}>Run now</button>
              <button onClick={hideWidget}>×</button>
            </div>
          </div>

          {error && <div className="panel-error">{error}</div>}

          {brief && (
            <div className="panel-body">
              <BriefSection title="Urgent" items={brief.urgent} />
              <BriefSection title="Deadlines" items={brief.deadlines} />
              <BriefSection title="Needs Reply" items={brief.needs_reply} />
              <BriefSection title="Other Action Items" items={brief.other_action_items} />
              {actionCount === 0 && <p className="empty">Nothing needs your attention right now.</p>}
            </div>
          )}
        </div>
      )}
    </div>
  );
}

function BriefSection({ title, items }: { title: string; items: DailyBrief["urgent"] }) {
  if (items.length === 0) return null;
  return (
    <section>
      <h4>{title}</h4>
      <ul>
        {items.map((item) => (
          <li key={item.message_id}>
            <span className="subject">{item.subject}</span>
            <span className="summary">{item.summary}</span>
          </li>
        ))}
      </ul>
    </section>
  );
}

export default App;
