import React from 'react';

export default function DisplayCard({ display, visible, flash, onDismiss }) {
  if (!display || !display.content || !visible) return null;

  return (
    <div className={"display-card" + (flash ? ' flash' : '')}>
      <div className="display-header">
        <span className="display-icon">{display.format === 'html' ? '\ud83d\udcfa' : '\ud83d\udcc4'}</span>
        <span className="display-fmt">{display.format}</span>
        {display.title && <span className="display-title">{display.title}</span>}
        <span className="display-ts">{new Date(display.ts * 1000).toLocaleTimeString()}</span>
        <button className="display-dismiss" onClick={onDismiss}>{'\u2715'}</button>
      </div>
      <div className="display-content">
        {display.format === 'text' && <pre className="display-text">{display.content}</pre>}
        {display.format === 'code' && <pre className="display-code"><code>{display.content}</code></pre>}
        {(display.format === 'ascii' || display.format === 'table') && <pre className="display-mono">{display.content}</pre>}
        {display.format === 'markdown' && <pre className="display-md">{display.content}</pre>}
        {display.format === 'html' && <iframe srcDoc={display.content} className="display-html" scrolling="no" />}
      </div>
    </div>
  );
}
