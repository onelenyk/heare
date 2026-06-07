import React, { useState, useEffect, useRef } from 'react';

const UserVoiceBar = React.memo(function UserVoiceBar({ voiceState }) {
  const [effectiveState, setEffectiveState] = useState("idle");
  const sinceRef = useRef(0);

  useEffect(() => {
    if (!voiceState) return;
    sinceRef.current = voiceState.since_ts || 0;
    setEffectiveState(voiceState.state || "idle");
  }, [voiceState]);

  // Auto-decay "result" to idle after 4s
  useEffect(() => {
    if (effectiveState !== "result") return;
    const elapsed = (Date.now() / 1000) - sinceRef.current;
    const remaining = Math.max(0, 4000 - elapsed * 1000);
    if (remaining <= 0) { setEffectiveState("idle"); return; }
    const t = setTimeout(() => setEffectiveState("idle"), remaining);
    return () => clearTimeout(t);
  }, [effectiveState]);

  const labels = {
    idle: "\u25cb idle",
    listening: "\u25cf listening",
    stt: "\u25d0 transcribing",
    result: "\u2713 result"
  };
  const subs = {
    idle: "(no audio)",
    listening: "(speak now)",
    stt: voiceState?.last_partial ? voiceState.last_partial.substring(0, 40) : "(processing...)",
    result: voiceState?.last_final ? voiceState.last_final.substring(0, 40) : ""
  };

  return (
    <div>
      <div className="bar-label">{labels[effectiveState] || "\u25cb idle"}</div>
      <div className={"voice-bar voice-" + effectiveState}></div>
      <div className="bar-sub">{subs[effectiveState] || ""}</div>
    </div>
  );
});

export default UserVoiceBar;
