import React, { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import SetupModal from './components/SetupModal'

export const API = "http://127.0.0.1:9780"

// ── Permissions Gate ──
// Checks microphone availability before allowing dashboard access.
function PermissionsGate({ onReady, onSkip }) {
  const [micOk, setMicOk] = useState(null)
  const [reason, setReason] = useState("")

  useEffect(() => {
    checkMic()
    const t = setInterval(checkMic, 2000)
    return () => clearInterval(t)
  }, [])

  async function checkMic() {
    try {
      const r = await fetch(API + "/mic/status")
      const d = await r.json()
      // "unknown" means the daemon could not tell (no sounddevice in a
      // frozen build, an odd host) — that is not a reason to hide a
      // working dashboard behind a permission screen. Only a definite
      // no keeps the gate closed.
      const definite = d.mic_available || d.reason === "unknown"
      setReason(d.reason || "")
      setMicOk(definite)
      if (definite) onReady()
    } catch(e) {
      setMicOk(null)
    }
  }

  if (micOk === null) {
    return (
      <div style={{display:"flex",alignItems:"center",justifyContent:"center",minHeight:"100vh",padding:20}}>
        <div className="loading-skeleton">
          <div style={{fontSize:48,marginBottom:16}}>{'\ud83c\udfa4'}</div>
          <div className="loading-pulse">connecting to Heare{'\u2026'}</div>
        </div>
      </div>
    )
  }

  if (!micOk) {
    return (
      <div style={{display:"flex",alignItems:"center",justifyContent:"center",minHeight:"100vh",padding:20}}>
        <div style={{background:"var(--card)",border:"1px solid var(--border)",borderRadius:12,padding:32,maxWidth:480,width:"100%",textAlign:"center"}}>
          <div style={{fontSize:48,marginBottom:16}}>{'\ud83c\udfa4'}</div>
          <h2 style={{marginBottom:8}}>
            {reason === "no_input_device" ? "No Microphone Found" : "Microphone Access Required"}
          </h2>
          <p style={{color:"var(--muted)",marginBottom:20,lineHeight:1.6}}>
            {reason === "no_input_device" ? (
              <>Heare cannot see an input device.<br/>Plug a microphone in, or pick one in the audio settings.</>
            ) : (
              <>
                Heare needs microphone access to hear you.<br/>
                Open <strong>System Settings {'\u2192'} Privacy & Security {'\u2192'} Microphone</strong><br/>
                and enable Heare.
              </>
            )}
          </p>
          <p style={{color:"var(--accent)",fontSize:14}}>Waiting for permission{'\u2026'}</p>
          {/* A machine with no microphone must still be able to reach the
              dashboard \u2014 otherwise a missing mic also locks the user out
              of the API keys, the logs and everything else. */}
          <button className="btn" style={{marginTop:16}} onClick={onSkip}>
            Continue without a microphone
          </button>
        </div>
      </div>
    )
  }

  return <div style={{display:"flex",alignItems:"center",justifyContent:"center",minHeight:"100vh",color:"var(--muted)",fontSize:18}}>Loading{'\u2026'}</div>
}

// ── App ──
function App() {
  const [view, setView] = useState("loading")
  const [showSetup, setShowSetup] = useState(false)
  const [setupChecked, setSetupChecked] = useState(false)
  const [micReady, setMicReady] = useState(false)
  const [boot, setBoot] = useState(null)

  useEffect(() => {
    checkStatus()
    const t = setInterval(checkStatus, 2000)
    return () => clearInterval(t)
  }, [])

  async function checkStatus() {
    try {
      await fetch(API + "/settings/status")
      setView("dashboard")
    } catch(e) {
      setView("loading")
    }
  }

  // The daemon's own account of its boot (State key `boot_status`,
  // written by src/daemon/spine_engine.py). A daemon waiting for an API
  // key used to be indistinguishable from one that had hung.
  useEffect(() => {
    if (view !== "dashboard") return
    let alive = true
    const read = async () => {
      try {
        const r = await fetch(API + "/state")
        const d = await r.json()
        if (!alive) return
        setBoot(d.boot_status ? JSON.parse(d.boot_status) : null)
      } catch(e) { /* the dashboard's own poll reports the outage */ }
    }
    read()
    const t = setInterval(read, 2000)
    return () => { alive = false; clearInterval(t) }
  }, [view])

  // Check setup status when dashboard is active (first visit)
  useEffect(() => {
    if (view === "dashboard" && !setupChecked) {
      fetch(API + '/api/setup')
        .then(r => r.json())
        .then(d => {
          setSetupChecked(true)
          if (!d.setup_complete) setShowSetup(true)
        })
        .catch(() => setSetupChecked(true))
    }
  }, [view, setupChecked])

  function handleOpenSetup() { setShowSetup(true) }

  if (view === "loading") {
    return (
      <div style={{display:"flex",alignItems:"center",justifyContent:"center",minHeight:"100vh",padding:20}}>
        <div className="loading-skeleton">
          <div style={{fontSize:48,marginBottom:16}}>{'\ud83c\udfa4'}</div>
          <div className="loading-pulse">connecting to Heare{'\u2026'}</div>
        </div>
      </div>
    )
  }

  // The gate was written and never rendered: with no microphone
  // permission the user got a normal-looking dashboard and an assistant
  // that heard nothing.
  if (!micReady) {
    return <PermissionsGate onReady={() => setMicReady(true)} onSkip={() => setMicReady(true)} />
  }

  const waitingForKeys = boot && boot.ok === false && (boot.missing || []).length > 0

  return (
    <>
      {waitingForKeys && (
        <div style={{background:"var(--card)",borderBottom:"1px solid var(--accent-red, #c0392b)",padding:"10px 16px",display:"flex",alignItems:"center",gap:12,flexWrap:"wrap"}}>
          <span style={{fontSize:18}}>{'🔑'}</span>
          <span style={{flex:1,minWidth:220,fontSize:13,lineHeight:1.5}}>
            <strong>Waiting for {boot.missing.join(", ")}</strong>
            {boot.hint ? <span style={{color:"var(--muted)"}}> {'—'} {boot.hint}</span> : null}
            {(boot.costs || []).map(c => (
              <div key={c} style={{color:"var(--muted)",fontSize:11}}>{c}</div>
            ))}
          </span>
          <button className="btn primary" onClick={handleOpenSetup}>Add key</button>
        </div>
      )}
      <Dashboard onOpenSetup={handleOpenSetup} />
      <SetupModal show={showSetup} onClose={() => setShowSetup(false)} onComplete={() => { setShowSetup(false); setSetupChecked(true) }} />
    </>
  )
}

export default App
