import React, { useState, useEffect } from 'react'
import Dashboard from './components/Dashboard'
import SetupModal from './components/SetupModal'

export const API = "http://127.0.0.1:9780"

// ── Permissions Gate ──
// Checks microphone availability before allowing dashboard access.
function PermissionsGate({ onReady }) {
  const [micOk, setMicOk] = useState(null)

  useEffect(() => {
    checkMic()
    const t = setInterval(checkMic, 2000)
    return () => clearInterval(t)
  }, [])

  async function checkMic() {
    try {
      const r = await fetch(API + "/mic/status")
      const d = await r.json()
      setMicOk(d.mic_available)
      if (d.mic_available) onReady()
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
          <h2 style={{marginBottom:8}}>Microphone Access Required</h2>
          <p style={{color:"var(--muted)",marginBottom:20,lineHeight:1.6}}>
            Heare needs microphone access to hear you.<br/>
            Open <strong>System Settings {'\u2192'} Privacy & Security {'\u2192'} Microphone</strong><br/>
            and enable Heare.
          </p>
          <p style={{color:"var(--accent)",fontSize:14}}>Waiting for permission{'\u2026'}</p>
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

  return (
    <>
      <Dashboard onOpenSetup={handleOpenSetup} />
      <SetupModal show={showSetup} onClose={() => setShowSetup(false)} onComplete={() => { setShowSetup(false); setSetupChecked(true) }} />
    </>
  )
}

export default App
