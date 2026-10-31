# KYC Renewals UI

React + Vite + TypeScript front end for the backend in `../backend`. Dummy data only.

```bash
npm install
npm run dev        # http://localhost:5173 ; /api/* is forwarded to FastAPI on :8000
npm run build      # type-check and production build into dist/
npm run lint
```

The API (`uvicorn kyc.api.app:app`) and the worker (`python -m kyc.worker`) must be running; see the root README.
Set `KYC_API_URL` to forward to a different API address.

Sign in with a seeded account (password `kyc-demo-123`; the login page lists them). The session is an
httpOnly cookie set by the API, so the UI never handles the token.

`npm run e2e` runs the Playwright tests in the installed Chrome; it starts the API, worker and dev server
itself and reseeds the database (Postgres must be running).
