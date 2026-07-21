import React, { useState, useEffect, useCallback } from 'react';

export default function AudioPanel({ state, post, onClose }) {
  const [vad, setVad] = useState(0.5);
  const [gain, setGain] = useState(1.0);
  const [volume, setVolume] = useState(1.0);
  const [sidetone, setSidetone] = useState(false);
  const [debounce, setDebounce] = useState(null);

  useEffect(() => {
    if (state.vad_sensitivity != null) setVad(parseFloat(state.vad_sensitivity));
    if (state.input_gain != null) setGain(parseFloat(state.input_gain));
    if (state.output_volume != null) setVolume(parseFloat(state.output_volume));
    if (state.sidetone != null) setSidetone(state.sidetone === '1' || state.sidetone === true);
  }, [state.vad_sensitivity, state.input_gain, state.output_volume, state.sidetone]);

  const send = useCallback((key, value) => {
    clearTimeout(debounce);
    const t = setTimeout(() => post('/state', { key, value: String(value) }), 150);
    setDebounce(t);
  }, [post, debounce]);

  const toggleSidetone = useCallback(() => {
    const next = !sidetone;
    setSidetone(next);
    post('/state', { key: 'sidetone', value: next ? '1' : '0' });
  }, [sidetone, post]);

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
            <span>VAD sensitivity</span>
            <span style={valueStyle}>{(vad * 100).toFixed(0)}%</span>
          </div>
          <div style={{ fontSize: 10, color: 'var(--muted)', marginBottom: 4 }}>
            How easily speech is detected. Lower catches whispers, higher needs a clear voice.
          </div>
          <input
            type="range"
            min="0"
            max="1"
            step="0.05"
            value={vad}
            onChange={e => { setVad(Number(e.target.value)); send('vad_sensitivity', e.target.value); }}
            style={sliderStyle}
          />
          <div style={{ fontSize: 10, color: 'var(--muted)', display: 'flex', justifyContent: 'space-between' }}>
            <span>sensitive</span>
            <span>strict</span>
          </div>
        </div>

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

        <div className="controls-sep" style={{ margin: 'var(--s3) 0' }}></div>

        <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between' }}>
          <div>
            <div style={{ fontSize: 12, fontWeight: 600 }}>Sidetone</div>
            <div style={{ fontSize: 10, color: 'var(--muted)', marginTop: 1 }}>
              Hear yourself through speakers
            </div>
          </div>
          <button
            className={'btn' + (sidetone ? ' active' : '')}
            onClick={toggleSidetone}
            style={{ minWidth: 80 }}
          >
            {sidetone ? '\uD83D\uDD0A on' : '\uD83D\uDD07 off'}
          </button>
        </div>

        {sidetone && (
          <div style={{
            marginTop: 'var(--s2)',
            padding: 'var(--s2)',
            background: 'rgba(126,231,135,0.06)',
            border: '1px solid rgba(126,231,135,0.15)',
            borderRadius: 'var(--r-xs)',
            fontSize: 10,
            color: 'var(--accent)',
          }}>
            {'\u2139\uFE0F'} Sidetone is active — you will hear your mic input.
            Gated off while the bot is speaking to prevent feedback.
          </div>
        )}
      </div>
    </div>
  );
}
