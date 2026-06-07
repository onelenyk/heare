import React from 'react';

const UsageCard = React.memo(function UsageCard({ usage }) {
  if (!usage) return <div className="info-line">no usage data</div>;

  return (
    <div>
      <div className="usage-line">
        <span className="label">llm</span>
        <span className="value">
          {usage.llm_calls || 0} calls / {(usage.llm_input_tokens || 0) + (usage.llm_output_tokens || 0)} tok / ${(usage.llm_cost_usd || 0).toFixed(4)}
        </span>
      </div>
      <div className="usage-line">
        <span className="label">stt</span>
        <span className="value">
          {usage.stt_calls || 0} calls / {usage.stt_audio_seconds || 0}s / ${(usage.stt_cost_usd || 0).toFixed(4)}
        </span>
      </div>
      <div className="usage-line">
        <span className="label">tts</span>
        <span className="value">
          {usage.tts_calls || 0} calls / {usage.tts_char_count || 0} ch / ${(usage.tts_cost_usd || 0).toFixed(4)}
        </span>
      </div>
      <div className="usage-line">
        <span className="label usage-total">total</span>
        <span className="value usage-total">${(usage.total_cost_usd || 0).toFixed(4)}</span>
      </div>
    </div>
  );
});

export default UsageCard;
