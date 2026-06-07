import React, { useState, useEffect, useRef } from 'react';

const AgentStatusBar = React.memo(function AgentStatusBar({ agentState }) {
  const [effectiveState, setEffectiveState] = useState("idle");
  const sinceRef = useRef(0);

  useEffect(() => {
    if (!agentState) return;
    sinceRef.current = agentState.since_ts || 0;
    setEffectiveState(agentState.state || "idle");
  }, [agentState]);

  // Auto-decay "interrupted" to idle after 4s
  useEffect(() => {
    if (effectiveState !== "interrupted") return;
    const elapsed = (Date.now() / 1000) - sinceRef.current;
    const remaining = Math.max(0, 4000 - elapsed * 1000);
    if (remaining <= 0) { setEffectiveState("idle"); return; }
    const t = setTimeout(() => setEffectiveState("idle"), remaining);
    return () => clearTimeout(t);
  }, [effectiveState]);

  const labels = {
    idle: "\u25cb idle",
    talking: "\u25cf talking",
    interrupted: "\u26a1 interrupted"
  };
  const subs = {
    idle: "(waiting...)",
    talking: "(speaking)",
    interrupted: "(interrupted)"
  };

  return (
    <div>
      <div className="bar-label">{labels[effectiveState] || "\u25cb idle"}</div>
      <div className={"agent-bar agent-" + effectiveState}></div>
    </div>
  );
});

export default AgentStatusBar;
