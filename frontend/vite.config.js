import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
//
// vite-plugin-pwa is already installed (package.json) but deliberately
// NOT wired in here yet — it needs a real src/service-worker.js to point
// at, which is Phase D's job (offline pre-staging / local-fire logic).
// Wiring it in now against a file that doesn't exist yet would just
// break the build for no benefit. Revisit this file in Phase D.
export default defineConfig({
  plugins: [react()],
})
