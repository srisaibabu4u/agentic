// End-to-end tests: the real UI in the installed Chrome, against the real API, worker and Postgres.
// By default they start the dev servers (Postgres must be running: docker compose up -d db) and reseed the
// database before the worker starts. With E2E_BASE_URL set they test that running stack instead, e.g. the
// Docker Compose stack on a fresh volume.
import path from 'node:path'
import { defineConfig } from '@playwright/test'

const backend = path.resolve(import.meta.dirname, '../backend')
const python = path.resolve(import.meta.dirname, process.platform === 'win32' ? '../venv/Scripts/python.exe' : '../venv/bin/python')

const external = process.env.E2E_BASE_URL

export default defineConfig({
  testDir: 'e2e',
  timeout: 180_000,
  expect: { timeout: 60_000 }, // the agents need a few seconds per step
  workers: 1,
  reporter: [['list']],
  use: { baseURL: external ?? 'http://localhost:5173', channel: 'chrome', headless: true, trace: 'retain-on-failure' },
  webServer: external ? undefined : [
    { command: `"${python}" -m uvicorn kyc.api.app:app --port 8000`, cwd: backend,
      url: 'http://localhost:8000/health', reuseExistingServer: false, timeout: 60_000 },
    // reseed, then start the worker: in one command so the worker never sees the data being replaced
    { command: `"${python}" -m kyc.seed --clients 25 --reset && "${python}" -m kyc.worker --poll 0.3`, cwd: backend,
      env: { PYTHONIOENCODING: 'utf-8' },
      wait: { stderr: /worker .* started/ }, reuseExistingServer: false, timeout: 60_000 },
    { command: 'npx vite --port 5173 --strictPort', url: 'http://localhost:5173',
      reuseExistingServer: false, timeout: 60_000 },
  ],
})
