import React from 'react';

export default function AudioDevicePicker({ devices, activeInput, activeOutput, onSelect, onClose }) {
  if (!devices || devices.length === 0) {
    return (
      <div className="card">
        <div className="card-header">
          audio devices <button className="modal-close" onClick={onClose} style={{marginLeft: "auto"}}>{'\u00d7'}</button>
        </div>
        <div className="info-line" style={{marginBottom: 6}}>
          No devices found or sounddevice not installed.
        </div>
      </div>
    );
  }

  return (
    <div className="card">
      <div className="card-header">
        audio devices <span style={{fontWeight: 400, color: "var(--muted)"}}>{'\u2014'} click to select</span>
        <button className="modal-close" onClick={onClose} style={{marginLeft: "auto"}}>{'\u00d7'}</button>
      </div>
      <div className="scroll" style={{maxHeight: 240}}>
        <table className="audio-list">
          <thead>
            <tr><th>idx</th><th>name</th><th>i/o</th></tr>
          </thead>
          <tbody>
            {devices.map(d => {
              const isActiveIn = activeInput && d.name.toLowerCase().includes(activeInput.toLowerCase());
              const isActiveOut = activeOutput && d.name.toLowerCase().includes(activeOutput.toLowerCase());
              const isActive = isActiveIn || isActiveOut;
              const kindIn = d.max_input_channels > 0 ? "input" : null;
              const kindOut = d.max_output_channels > 0 ? "output" : null;
              return (
                <tr key={d.index} className={isActive ? "active" : ""}>
                  <td style={{color: "var(--muted)"}}>{d.index}</td>
                  <td style={{overflow: "hidden", textOverflow: "ellipsis", maxWidth: 240}}
                      title={d.name}
                      onClick={() => {
                        if (kindIn) onSelect({device_index: d.index, device_name: d.name, kind: "input"});
                        if (kindOut) onSelect({device_index: d.index, device_name: d.name, kind: "output"});
                      }}>
                    {d.name}
                  </td>
                  <td>
                    {kindIn && <span className={"badge in" + (isActiveIn ? " active" : "")}>IN{isActiveIn ? ' \u2713' : ''}</span>}
                    {kindOut && <span className={"badge out" + (isActiveOut ? " active" : "")}>OUT{isActiveOut ? ' \u2713' : ''}</span>}
                  </td>
                </tr>
              );
            })}
          </tbody>
        </table>
      </div>
      <div style={{marginTop: 6}}>
        <button className="btn" onClick={onClose}>close</button>
      </div>
    </div>
  );
}
