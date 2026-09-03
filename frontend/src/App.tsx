import MapView from "./components/MapView"
import { useState, useEffect } from "react"
function App() {
  const [health, setHealth] = useState("bad")
  useEffect(() => {
    fetch('/api/health')
    .then(res => res.json())
    .then(data => setHealth(data.status))
  }, [])

  return (
    <div>
      <MapView/>
    </div>
  )
}

export default App
