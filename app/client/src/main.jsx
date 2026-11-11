import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/lumi-tokens.css'
import './styles/lumi-app.css'
import './index.css'
import App from './App.jsx'
import { applyLowPower } from './lib/lowPower'
import { applyTheme } from './lib/themeMode'

applyLowPower() // before the first paint, so a weak machine never renders the blur
applyTheme() // light / dark / system, before the first paint too: no flash of the other theme

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
