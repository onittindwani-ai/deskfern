from deskfern import ingest
from deskfern.models import Source
from tests.conftest import login


def test_repeated_bad_sign_ins_are_slowed(client):
    codes = [client.post("/api/auth/login", json={"email": "owner@example.test", "password": "wrong-password-x"}).status_code for _ in range(12)]
    assert 429 in codes, codes


def test_a_damaged_pdf_and_a_link_out_of_the_folder_are_skipped_not_fatal(ctx, tmp_path):
    folder = tmp_path / "kb"
    folder.mkdir()
    (folder / "ok.md").write_text("# Plans\n\nThe team plan has 3 seats.\n")
    (folder / "broken.pdf").write_bytes(b"%PDF-1.4\nnot a pdf")
    outside = tmp_path / "secret.md"
    outside.write_text("# Secret\n\nnot for the desk\n")
    (folder / "link.md").symlink_to(outside)
    with ctx.factory() as s:
        src = Source(kind="folder", name="t", uri=str(folder))
        s.add(src)
        s.flush()
        counts = ingest.ingest_folder(s, src, folder)
    assert counts["new"] == 1 and counts["skipped"] == 2, counts


def test_the_session_is_required_on_every_write(client):
    for path in ["/api/demo/tick", "/api/demo/reset", "/api/approvals/1/decide"]:
        assert client.post(path, json={}).status_code == 401, path
