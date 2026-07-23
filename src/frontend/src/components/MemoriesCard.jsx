import React, { useState, useEffect, useCallback } from 'react';
import { API } from '../App';

const TYPE_COLORS = {
  fact: '#8b5cf6',
  preference: '#06b6d4',
  decision: '#f59e0b',
  event: '#6b7280',
};

function fmtTs(ts) {
  if (!ts) return '';
  const d = new Date(ts * 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${d.getMonth() + 1}/${d.getDate()} ${pad(d.getHours())}:${pad(d.getMinutes())}`;
}

const MemoriesCard = React.memo(function MemoriesCard({ onToast }) {
  const [memories, setMemories] = useState([]);
  const [loading, setLoading] = useState(true);
  const [forgetting, setForgetting] = useState(new Set());

  const fetchMemories = useCallback(async () => {
    try {
      const r = await fetch(API + '/api/memories?limit=50');
      const d = await r.json();
      setMemories(d.memories || []);
    } catch (e) {
      if (onToast) onToast('failed to load memories: ' + e.message, 'err');
    } finally {
      setLoading(false);
    }
  }, [onToast]);

  useEffect(() => {
    fetchMemories();
  }, [fetchMemories]);

  async function handleForget(id) {
    setForgetting((prev) => new Set(prev).add(id));
    try {
      const r = await fetch(API + '/api/memories/' + id + '/forget', { method: 'POST' });
      const d = await r.json();
      if (d.ok) {
        setMemories((prev) => prev.filter((m) => m.id !== id));
        if (onToast) onToast('memory forgotten', 'ok');
      } else {
        if (onToast) onToast(d.error || 'forget failed', 'err');
      }
    } catch (e) {
      if (onToast) onToast('forget failed: ' + e.message, 'err');
    } finally {
      setForgetting((prev) => {
        const next = new Set(prev);
        next.delete(id);
        return next;
      });
    }
  }

  if (loading) return <div className="info-line">loading memories...</div>;
  if (memories.length === 0) return <div className="info-line">no memories yet</div>;

  return (
    <div className="memories-list">
      {memories.map((m) => (
        <div key={m.id} className="memory-row">
          <span
            className="memory-type"
            style={{ color: TYPE_COLORS[m.type] || '#6b7280' }}
          >
            [{m.type}]
          </span>
          <span className="memory-content">{m.content}</span>
          <span className="memory-ts">{fmtTs(m.created_ts)}</span>
          <button
            className="memory-forget"
            onClick={() => handleForget(m.id)}
            disabled={forgetting.has(m.id)}
          >
            {forgetting.has(m.id) ? '...' : 'forget'}
          </button>
        </div>
      ))}
    </div>
  );
});

export default MemoriesCard;
