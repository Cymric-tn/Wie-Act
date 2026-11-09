import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/lumi-tokens.css'
import './styles/lumi-app.css'
import './index.css'
import App from './App.jsx'
import { applyLowPower } from './lib/lowPower'

applyLowPower() // before the first paint, so a weak machine never renders the blur

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
