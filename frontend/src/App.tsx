import { useState, useEffect } from "react"
import MapView from "./components/MapView"

// The map is served as static files and the routes are not. So the tiles can be
// on screen, looking perfectly healthy, while nothing is there to answer a
// click -- and the first sign of it would otherwise be a failed route two
// clicks later. Ask once, up front, and say so.
type Health = "checking" | "ok" | "down"

function App() {
  const [health, setHealth] = useState<Health>("checking")

  useEffect(() => {
    const controller = new AbortController()

    fetch('/api/health', { signal: controller.signal })
      .then(res => res.json())
      .then(data => setHealth(data.status === 'ok' ? 'ok' : 'down'))
      // A refused connection rejects rather than returning a status, so this
      // is the branch a backend that is simply not running arrives in.
      .catch((err: Error) => {
        if (err.name !== 'AbortError') setHealth('down')
      })

    return () => controller.abort()
  }, [])

  return (
    <>
      <MapView />
      {/* "checking" shows nothing: a badge that flashes up on every load while
          a healthy backend answers is worse than no badge at all. */}
      {health === "down" && (
        <div className="panel status" role="status">
          <span className="status-dot" />
          Routing service offline — the map still works, but walks can't be planned.
        </div>
      )}
    </>
  )
}

export default App
