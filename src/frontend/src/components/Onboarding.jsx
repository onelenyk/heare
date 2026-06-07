import React, { useState } from "react";
import { API } from "../App";

export default function Onboarding({ onSaved }) {
  const [groq, setGroq] = useState("");
  const [llm, setLlm] = useState("");
  const [provider, setProvider] = useState("deepseek_api_key");
  const [lang, setLang] = useState("en");
  const [voice, setVoice] = useState("en-US-AriaNeural");
  const [status, setStatus] = useState("");
  const [saving, setSaving] = useState(false);

  const PROVIDER_NAMES = {
    deepseek_api_key: "DEEPSEEK_API_KEY",
    openrouter: "OPENROUTER_API_KEY",
    zai: "ZAI_API_KEY",
    opencode: "OPENCODE_API_KEY",
  };

  async function handleSubmit(e) {
    e.preventDefault();
    if (!groq.trim() && !llm.trim()) {
      setStatus("At least one API key is required");
      return;
    }
    setSaving(true);
    setStatus("Saving\u2026");
    try {
      const body = {
        groq_api_key: groq.trim(),
        language: lang,
        tts_voice: voice,
      };
      body[provider] = llm.trim();
      const r = await fetch(API + "/settings", {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(body),
      });
      const d = await r.json();
      if (!d.ok) {
        setStatus(d.errors ? d.errors.join(". ") : "Failed to save settings");
        setSaving(false);
        return;
      }
      setStatus("Keys saved! Starting daemon\u2026");
      setTimeout(() => onSaved(), 1000);
    } catch (e) {
      setStatus("Failed to save \u2014 is the daemon running?");
      setSaving(false);
    }
  }

  return (
    <div className="onboard-wrap">
      <form className="onboard-card" onSubmit={handleSubmit}>
        <h1>heare</h1>
        <div className="sub">Proactive ambient voice AI assistant</div>

        <div className="field">
          <label>Groq API Key (Speech-to-Text)</label>
          <input
            type="password"
            value={groq}
            onInput={(e) => setGroq(e.target.value)}
            placeholder="gsk_..."
            autoFocus
          />
          <div className="hint">
            <a href="https://console.groq.com/keys" target="_blank">
              Get a free key →
            </a>
          </div>
        </div>

        <div className="field">
          <label>LLM API Key</label>
          <input
            type="password"
            value={llm}
            onInput={(e) => setLlm(e.target.value)}
            placeholder="sk-..."
          />
          <div className="hint">
            <select
              value={provider}
              onChange={(e) => setProvider(e.target.value)}
              style={{
                width: "auto",
                background: "var(--bg)",
                color: "var(--text)",
                border: "1px solid var(--border)",
                padding: "2px 6px",
                fontSize: 11,
                borderRadius: 3,
              }}
            >
              <option value="deepseek_api_key">DeepSeek</option>
              <option value="openrouter">OpenRouter</option>
              <option value="zai">z.ai</option>
              <option value="opencode">OpenCode</option>
            </select>
          </div>
        </div>

        <hr className="divider" />

        <div className="field">
          <label>Language</label>
          <select value={lang} onChange={(e) => setLang(e.target.value)}>
            <option value="uk">Ukrainian</option>
            <option value="en">English</option>
            <option value="ru">Russian</option>
          </select>
        </div>

        <div className="field">
          <label>Voice</label>
          <select value={voice} onChange={(e) => setVoice(e.target.value)}>
            <option value="en-US-AriaNeural">en-US-AriaNeural</option>
            <option value="uk-UA-OstapNeural">
              uk-UA-OstapNeural (male)
            </option>
            <option value="uk-UA-PolinaNeural">
              uk-UA-PolinaNeural (female)
            </option>
            <option value="ru-RU-SvetlanaNeural">ru-RU-SvetlanaNeural</option>
          </select>
        </div>

        <button
          type="submit"
          className="btn primary"
          disabled={saving}
          style={{ width: "100%" }}
        >
          {saving ? "Saving\u2026" : "Save & Start Heare"}
        </button>
        <div
          className={"status-msg " + (status.includes("Failed") ? "err" : "ok")}
        >
          {status}
        </div>

        <div className="features">
          <div>
            <strong>🎤 Always listening</strong>VAD-gated
          </div>
          <div>
            <strong>🧠 Claude-powered</strong>Autonomous
          </div>
          <div>
            <strong>🌐 Browser control</strong>Chrome bridge
          </div>
          <div>
            <strong>🔧 Dev tools</strong>Bash, files, web
          </div>
        </div>
      </form>
    </div>
  );
}
