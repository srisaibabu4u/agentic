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

Until JWT login (Phase 4), the "Acting as" menu chooses the user sent in the `X-User` header.
