import Link from "next/link";

export default function NotFound() {
  return (
    <main className="lh-login" id="main">
      <div className="lh-login__card" style={{ textAlign: "center" }}>
        <h1 className="lh-title">Page not found</h1>
        <p className="lh-sub" style={{ margin: "8px auto 0" }}>There is nothing at this address. It may have been typed wrong or moved.</p>
        <p><Link href="/">Back to the queue</Link></p>
      </div>
    </main>
  );
}
