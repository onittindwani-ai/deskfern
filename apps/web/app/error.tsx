"use client";
import { Button } from "@sendora/ui-kit";

/** Shown when a page throws while it draws. The retry button asks Next to draw it again. */
export default function ErrorPage({ error, reset }: { error: Error & { digest?: string }; reset: () => void }) {
  return (
    <main className="lh-login" id="main" role="alert">
      <div className="lh-login__card" style={{ textAlign: "center" }}>
        <h1 className="lh-title">Something went wrong</h1>
        <p className="lh-sub" style={{ margin: "8px auto 21px" }}>The page could not be shown. Nothing was sent or changed by this error.{error.digest ? ` Reference: ${error.digest}.` : ""}</p>
        <Button variant="primary" onClick={() => reset()}>Try again</Button>
      </div>
    </main>
  );
}
