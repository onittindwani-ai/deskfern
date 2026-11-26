# Deploy the web app on Vercel

Vercel hosts only the **web** part. The API and worker need a server that stays on (Railway, Hetzner or any host that runs Docker). **Not tested: no Vercel account was used.** `next build` was run locally and passed.

The Root Directory steps were read from https://vercel.com/docs/monorepos on 2026-11-23 (VERIFIED). The rest is UNVERIFIED.

1. Put the API online first (`07` or `08`) and note its address, such as `https://api.example.com`.
2. In Vercel choose Create New, then Project, and import your Git repository.
3. Press Edit next to Root Directory and choose `apps/web`.
4. If the build cannot find `packages/ui-kit`, look for the setting that includes source files outside the Root Directory and turn it on. UNVERIFIED: the name of that setting was not on the page we read.
5. Framework Next.js. Install command `pnpm install`. Build command `pnpm build`.
6. Set `API_BASE_URL` to your API address with no trailing slash.
7. Do not set `INSECURE_COOKIES` or `NEXT_PUBLIC_DEMO_HINT`.
8. Deploy, open the address, sign in.
9. Tell the API who may say which address a visitor has. The web app on Vercel reaches your API from addresses you do not control, so the usual way is to keep the API private (behind the Hetzner Caddy or on a private network) and set `FORWARDED_ALLOW_IPS` on the API to the address range of whatever sits directly in front of it (the Docker image starts with `127.0.0.1` only). Run uvicorn with `--proxy-headers` (the Dockerfile does). If you leave this out, every visitor looks like the same address and the sign in limits are shared by everyone, so one bad try from a stranger can hold up the owner for a while. A browser that signed in before is not held up, and the settings page warns when it sees this. If a CDN such as Cloudflare sits in front of Caddy, tell Caddy to trust it so the real visitor is the last address in `x-forwarded-for`.

## If the build fails

Most often the shared `ui-kit` folder was not included (step 4), or `pnpm-workspace.yaml` does not list `packages/*`. See `15-TROUBLESHOOTING.md`.
