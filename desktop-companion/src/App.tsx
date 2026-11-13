import { useEffect, useRef, useState } from "react";
import { getCurrentWindow, LogicalSize } from "@tauri-apps/api/window";
import { sendNotification, isPermissionGranted, requestPermission } from "@tauri-apps/plugin-notification";
import { load } from "@tauri-apps/plugin-store";
import {
  fetchDailyBrief,
  fetchOpenItems,
  fetchStatus,
  startRun,
  type DailyBrief,
  type AgentStatus,
  type NagLevel,
  type OpenItem,
} from "./api";
import orbIcon from "./assets/man.png";
import "./App.css";

const DEFAULT_INTERVAL_MINUTES = 30;

function countActionItems(brief: DailyBrief): number {
  return brief.urgent.length + brief.deadlines.length + brief.needs_reply.length + brief.other_action_items.length;
}

const NAG_RANK: NagLevel[] = ["quiet", "noticeable", "persistent"];

function formatAge(hours: number): string {
  return hours < 24 ? `${Math.floor(hours)}h` : `${Math.floor(hours / 24)}d`;
}

function App() {
  const [openItems, setOpenItems] = useState<OpenItem[]>([]);
  const notifiedRef = useRef(new Set<string>());
  const [status, setStatus] = useState<AgentStatus | null>(null);
  const [brief, setBrief] = useState<DailyBrief | null>(null);
  const [panelOpen, setPanelOpen] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  async function refresh(notify: boolean) {
    setChecking(true);
    try {
      const [s, b, items] = await Promise.all([fetchStatus(), fetchDailyBrief(), fetchOpenItems()]);
      setStatus(s);
      setBrief(b);
      setOpenItems(items);
      setError(null);

      // One native notification per item (until app restart), never repeated per poll.
      const fresh = items.filter((i) => i.nag_level !== "quiet" && !notifiedRef.current.has(i.message_id));
      if (fresh.length > 0 || notify) await getCurrentWindow().show();
      // Notify only when the set changes: items already notified are skipped,
      // so an unchanged set stays silent on every poll.
      if (fresh.length > 0) {
        const granted = (await isPermissionGranted()) || (await requestPermission()) === "granted";
        if (granted) {
          fresh.forEach((i) => {
            notifiedRef.current.add(i.message_id);
            sendNotification({
              title: "Reply overdue",
              body: `${i.sender_address} — "${i.subject}" waiting ${formatAge(i.age_hours)}`,
            });
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
    let cancelled = false;
    refresh(false);

    (async () => {
      const store = await load("companion-settings.json");
      let minutes = await store.get<number>("interval_minutes");
      if (!minutes || minutes <= 0) {
        minutes = DEFAULT_INTERVAL_MINUTES;
        await store.set("interval_minutes", minutes);
        await store.save();
      }
      if (!cancelled) timerRef.current = setInterval(() => refresh(true), minutes * 60 * 1000);
    })();

    return () => {
      cancelled = true;
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

  const nagLevel = openItems.reduce<NagLevel>(
    (top, i) => (NAG_RANK.indexOf(i.nag_level) > NAG_RANK.indexOf(top) ? i.nag_level : top),
    "quiet",
  );
  // Any noticeable/persistent item disables both auto-dismiss timers until it
  // is replied/dismissed (the endpoint then stops returning it).
  const pinned = nagLevel !== "quiet";

  useEffect(() => {
    if (!panelOpen || pinned) return;
    const timeout = setTimeout(() => setPanel(false), 5000);
    return () => clearTimeout(timeout);
  }, [panelOpen, pinned]);

  // The orb itself auto-hides after a glance; it reappears on the next
  // scheduled refresh (or via the tray's "Show") rather than staying pinned
  // on screen between checks.
  useEffect(() => {
    if (panelOpen || pinned) return;
    const timeout = setTimeout(() => getCurrentWindow().hide(), 30000);
    return () => clearTimeout(timeout);
  }, [panelOpen, brief, pinned]);

  async function hideWidget() {
    await getCurrentWindow().hide();
  }

  const actionCount = brief ? countActionItems(brief) : 0;
  const widgetMood = error ? "error" : nagLevel !== "quiet" ? nagLevel : actionCount > 0 ? "alert" : "calm";

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
              {openItems.length > 0 && (
                <section>
                  <h4>Awaiting your reply</h4>
                  <ul>
                    {openItems.map((i) => (
                      <li key={i.message_id}>
                        <span className="subject">{i.subject}</span>
                        <span className="summary">
                          {i.sender_address} · {formatAge(i.age_hours)}
                        </span>
                      </li>
                    ))}
                  </ul>
                </section>
              )}
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
