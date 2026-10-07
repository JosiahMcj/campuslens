import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import '@fontsource-variable/inter'
import './index.css'
import './theme-librechat.css'
import './chat.css'
import './sidebar-nav.css'
import './landing.css'
import App from './App.tsx'
import { ChartGallery } from './components/ChartGallery'
import { applyPrefs } from './theme'

// Before the first render, so the page never flashes the wrong theme.
applyPrefs()

createRoot(document.getElementById('root')!).render(
  <StrictMode>
    {/* /dev/charts: the chart templates with sample data (no API). */}
    {window.location.pathname === '/dev/charts' ? <ChartGallery /> : <App />}
  </StrictMode>,
)

// The service worker caches only the application shell, never an API
// response: offline, the shell opens and the briefing still requires the
// API. A failed registration is quiet; the app works without it.
if ('serviceWorker' in navigator) {
  window.addEventListener('load', () => {
    navigator.serviceWorker.register('/sw.js').catch(() => {})
  })
}
