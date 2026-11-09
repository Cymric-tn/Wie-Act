import { StrictMode } from 'react'
import { createRoot } from 'react-dom/client'
import './styles/lumi-tokens.css'
import './styles/lumi-app.css'
import './index.css'
import App from './App.jsx'

createRoot(document.getElementById('root')).render(
  <StrictMode>
    <App />
  </StrictMode>,
)
