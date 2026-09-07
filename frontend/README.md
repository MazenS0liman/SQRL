# Squirrel Frontend

React and TypeScript client for the Squirrel data workspace. In production it
is built into static assets and served by the FastAPI container.

## Local development

Install dependencies and start the Vite development server:

```powershell
npm ci
npm run dev
```

The client uses `/api` as its production API base. During local development,
configure the Vite proxy in `vite.config.ts` or run the backend on the expected
local API address.

## Commands

- `npm run dev` starts Vite with hot module replacement.
- `npm run build` creates the production bundle in `dist`.
- `npm run build:dev` creates a development-mode bundle.
- `npm run lint` runs ESLint.
- `npm run preview` serves the production bundle locally.

## Production build

From the repository root, `docker compose up --build` runs the frontend build
stage and serves the resulting application at `http://localhost:8000`.
