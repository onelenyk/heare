import React, { useEffect } from 'react';

export default function Toast({ message, type, onDismiss }) {
  useEffect(() => {
    const t = setTimeout(onDismiss, 3000);
    return () => clearTimeout(t);
  }, []);

  return (
    <div className={"toast toast-" + (type || "ok")}>
      <span style={{flex: 1, minWidth: 0}}>{message}</span>
      <button className="toast-close" onClick={onDismiss}>{'\u00d7'}</button>
    </div>
  );
}
