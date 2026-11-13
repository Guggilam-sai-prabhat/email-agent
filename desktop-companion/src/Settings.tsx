import { useEffect, useState } from "react";
import { openUrl } from "@tauri-apps/plugin-opener";
import {
  connectGmail,
  fetchSetupChecks,
  pullModel,
  setStoreValue,
  uploadGmailCredentials,
  type SetupCheck,
} from "./api";

const MAX_IMAGE_BYTES = 1_000_000;
const CONSOLE_URL = "https://console.cloud.google.com/apis/credentials";
const SYSTEM_IDS = ["disk", "ollama_installed", "ollama_running", "model"];

const readDataUrl = (f: File) =>
  new Promise<string>((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(r.result as string);
    r.onerror = () => reject(r.error);
    r.readAsDataURL(f);
  });

export default function Settings({ onChanged, onDone }: { onChanged: () => void; onDone: () => void }) {
  const [checks, setChecks] = useState<SetupCheck[]>([]);
  const [msg, setMsg] = useState<string | null>(null);

  async function recheck() {
    try {
      setChecks(await fetchSetupChecks());
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  // Re-check every few seconds so ✓ appear by themselves after the user installs
  // Ollama / the model download finishes / Google sign-in completes.
  useEffect(() => {
    recheck();
    const t = setInterval(recheck, 4000);
    return () => clearInterval(t);
  }, []);

  async function run(action: () => Promise<string | void>) {
    try {
      setMsg((await action()) ?? "Saved.");
      await recheck();
      onChanged();
    } catch (e) {
      setMsg(e instanceof Error ? e.message : String(e));
    }
  }

  async function pickImage(file: File) {
    if (!file.type.startsWith("image/")) throw new Error("Choose an image file (PNG or JPG).");
    if (file.size > MAX_IMAGE_BYTES) throw new Error("Image is over 1 MB. Choose a smaller one.");
    await setStoreValue("agent_image", await readDataUrl(file));
  }

  const byId = Object.fromEntries(checks.map((c) => [c.id, c]));
  const systemChecks = SYSTEM_IDS.map((id) => byId[id]).filter(Boolean);
  const systemOk = systemChecks.length === SYSTEM_IDS.length && systemChecks.every((c) => c.ok);
  const allOk = systemOk && checks.every((c) => c.ok);

  return (
    <div className="panel-body settings">
      <section>
        <h4>1. System check</h4>
        <ul className="checks">
          {systemChecks.map((c) => (
            <li key={c.id}>
              {c.ok ? "✓" : "✗"} {c.label}
              {c.detail && <span className="hint"> ({c.detail})</span>}
              {!c.ok && c.fix && <div className="hint">{c.fix}</div>}
            </li>
          ))}
        </ul>
        {!systemOk && byId.ollama_installed?.ok && !byId.model?.ok && (
          <button onClick={() => run(async () => { await pullModel(); return "Downloading the model. This can take a few minutes."; })}>
            Download model
          </button>
        )}
        {!systemOk && <button onClick={recheck}>Re-check</button>}
      </section>

      {systemOk && (
        <section>
          <h4>2. Google access</h4>
          <p className="hint">You need one file from Google, called <code>credentials.json</code>. To get it:</p>
          <ol className="hint">
            <li>
              Open{" "}
              <a href={CONSOLE_URL} onClick={(e) => { e.preventDefault(); openUrl(CONSOLE_URL); }}>
                Google Cloud → Credentials
              </a>{" "}
              and sign in with the Gmail account to watch.
            </li>
            <li>Create a project, then enable the <b>Gmail API</b>.</li>
            <li>Set up the consent screen: type External, and add your own Gmail as a test user.</li>
            <li><b>Create credentials → OAuth client ID → Desktop app</b>.</li>
            <li>Press <b>Download JSON</b> and choose that file below.</li>
          </ol>
          <input
            type="file"
            accept="application/json,.json"
            onChange={(e) => {
              const f = e.target.files?.[0];
              if (f) run(async () => { await uploadGmailCredentials(await f.text()); return "Credentials saved."; });
            }}
          />
          <p className="hint">{byId.gmail_credentials?.ok ? "✓ Credentials file saved." : "✗ Not uploaded yet."}</p>
          {byId.gmail_credentials?.ok && (
            <>
              <button
                onClick={() => run(async () => { await connectGmail(); return "A browser window opened. Approve read-only access there."; })}
              >
                {byId.gmail_connected?.ok ? "Reconnect Gmail" : "Connect Gmail"}
              </button>
              <p className="hint">
                {byId.gmail_connected?.ok
                  ? "✓ Gmail connected (read-only)."
                  : "✗ Not connected. Google will ask you to approve viewing your email only."}
              </p>
            </>
          )}
        </section>
      )}

      {systemOk && (
        <section>
          <h4>3. Agent picture (optional)</h4>
          <input type="file" accept="image/png,image/jpeg" onChange={(e) => { const f = e.target.files?.[0]; if (f) run(() => pickImage(f)); }} />
          <button onClick={() => run(() => setStoreValue("agent_image", null))}>Use default</button>
        </section>
      )}

      {msg && <p className="hint">{msg}</p>}
      {allOk && <button onClick={onDone}>All set — start</button>}
    </div>
  );
}
