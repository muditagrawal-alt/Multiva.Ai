# Multiva frontend

React 19 + TypeScript + Vite 8 + Tailwind v4 + Motion. Builds to `../web/`,
which the FastAPI service mounts at `/app`.

```bash
npm install
npm run dev      # localhost:5173, proxies the API to :8000
npm run build    # emits ../web
npx tsc --noEmit # typecheck
```

`npm run dev` proxies `/process_video`, `/jobs`, `/videos`, `/languages` and
`/api` to `http://127.0.0.1:8000`, so the dev server behaves exactly like the
production mount with no CORS shim.

## Notes

- `base: "/app/"` in `vite.config.ts` and `basename: "/app"` on the router must
  stay in step with the FastAPI mount point.
- Fonts live in `src/fonts/` rather than `public/` so Vite fingerprints them and
  rewrites the URLs for that base. In `public/` they resolve to the wrong path.
- `tsconfig.app.json` sets `erasableSyntaxOnly`, which bans constructor
  parameter properties and enums.
