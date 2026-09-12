import React, { useState, useEffect, useCallback } from 'react';

export default function AudioPanel({ state, post, onClose }) {
  const [gain, setGain] = useState(1.0);
  const [volume, setVolume] = useState(1.0);
  const [debounce, setDebounce] = useState(null);

  useEffect(() => {
    if (state.input_gain != null) setGain(parseFloat(state.input_gain));
    if (state.output_volume != null) setVolume(parseFloat(state.output_volume));
  }, [state.input_gain, state.output_volume]);

  const send = useCallback((key, value) => {
    clearTimeout(debounce);
    const t = setTimeout(() => post('/state', { key, value: String(value) }), 150);
    setDebounce(t);
  }, [post, debounce]);


  const sliderStyle = {
    width: '100%',
    margin: '2px 0 6px',
    accentColor: 'var(--accent)',
    height: 4,
    cursor: 'pointer',
  };

  const rowStyle = {
    marginBottom: 'var(--s3)',
  };

  const labelStyle = {
    display: 'flex',
    justifyContent: 'space-between',
    fontSize: 11,
    color: 'var(--muted)',
    marginBottom: 2,
  };

  const valueStyle = {
    color: 'var(--accent)',
    fontWeight: 700,
    minWidth: 40,
    textAlign: 'right',
  };

  return (
    <div className="card">
      <div className="card-header">
        {'\uD83C\uDF9B'} audio controls
        <button className="modal-close" onClick={onClose} style={{ marginLeft: 'auto' }}>
          {'\u00d7'}
        </button>
      </div>

      <div style={{ padding: 'var(--s2) var(--s3)' }}>

        <div style={rowStyle}>
          <div style={labelStyle}>
            <span>Mic gain</span>
            <span style={valueStyle}>{gain.toFixed(1)}x</span>
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>
            Amplifies your microphone before speech recognition. Useful if you're quiet.
          </div>
          <input
            type="range"
            min="0"
            max="5"
            step="0.1"
            value={gain}
            onChange={e => { setGain(Number(e.target.value)); send('input_gain', e.target.value); }}
            style={sliderStyle}
          />
          <div style={{ fontSize: 10, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between' }}>
            <span>0</span>
            <span>5x</span>
          </div>
        </div>

        <div style={rowStyle}>
          <div style={labelStyle}>
            <span>Speaker volume</span>
            <span style={valueStyle}>{(volume * 100).toFixed(0)}%</span>
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>
            How loud the bot speaks. Does not affect your system volume.
          </div>
          <input
            type="range"
            min="0"
            max="5"
            step="0.1"
            value={volume}
            onChange={e => { setVolume(Number(e.target.value)); send('output_volume', e.target.value); }}
            style={sliderStyle}
          />
          <div style={{ fontSize: 10, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between' }}>
            <span>0</span>
            <span>5x</span>
          </div>
        </div>

      </div>
    </div>
  );
}
