import { fileURLToPath } from "node:url";
import type { NextConfig } from "next";

/** What the browser may load. Everything is from this app's own address: no other site's script, no framing, no form posting elsewhere. Inline scripts and styles stay allowed
 *  because Next.js writes its page data and the app's own style attributes inline; a nonce based policy would need every page to be rendered per request. Development also needs
 *  eval and the live reload connection, so the policy is only sent by a production build. */
const CSP = [
  "default-src 'self'",
  "script-src 'self' 'unsafe-inline'",
  "style-src 'self' 'unsafe-inline'",
  "img-src 'self' data: blob:",
  "font-src 'self' data:",
  "connect-src 'self'",
  "object-src 'none'",
  "base-uri 'self'",
  "form-action 'self'",
  "frame-ancestors 'none'",
].join("; ");

const config: NextConfig = {
  transpilePackages: ["@sendora/ui-kit"],
  reactStrictMode: true,
  poweredByHeader: false,
  // Only Docker builds ask for the standalone folder (NEXT_STANDALONE=1); a normal build works with `next start`.
  output: process.env.NEXT_STANDALONE === "1" ? "standalone" : undefined,
  async headers() {
    // Defence in depth for the browser. The session cookie is already httpOnly and sameSite=lax.
    return [
      {
        source: "/:path*",
        headers: [
          ...(process.env.NODE_ENV === "production" ? [{ key: "Content-Security-Policy", value: CSP }] : []),
          { key: "X-Content-Type-Options", value: "nosniff" },
          { key: "X-Frame-Options", value: "DENY" },
          { key: "Referrer-Policy", value: "strict-origin-when-cross-origin" },
          { key: "Permissions-Policy", value: "camera=(), microphone=(), geolocation=()" },
        ],
      },
    ];
  },
  outputFileTracingRoot: fileURLToPath(new URL("../../", import.meta.url)),
};

export default config;
