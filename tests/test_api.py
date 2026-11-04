from fastapi.testclient import TestClient

from photo_metadata_agent.agent.worker import run_worker
from photo_metadata_agent.api.app import create_app


def test_upload_review_search_and_download(settings, image_bytes):
    with TestClient(create_app(settings)) as client:
        response = client.post(
            "/imports",
            files={"image": ("family.jpg", image_bytes, "image/jpeg")},
            data={"context": "Family picnic"},
            follow_redirects=False,
        )
        assert response.status_code == 303
        location = response.headers["location"]
        assert "Tags will appear here" in client.get(location).text
        run_worker(settings, once=True)
        page = client.get(location)
        assert "Verify the generated tags" in page.text
        assert "Demo run" in page.text
        assert client.get(location + "/preview").headers["content-type"] == "image/jpeg"
        assert client.get(location + "/metadata.xmp").status_code == 409
        response = client.post(
            location + "/review",
            data={
                "people_count_bucket": "4-6",
                "capture_style": "candid",
                "event": "yes",
                "ministry_trip": "no",
                "family": "yes",
                "notes": "Verified",
            },
            follow_redirects=False,
        )
        assert response.status_code == 303
        run_worker(settings, once=True)
        assert "Reviewed tags" in client.get(location).text
        assert "family.jpg" in client.get("/?q=people:4-6").text
        assert "family.jpg" not in client.get("/?q=people:10%2B").text
        assert client.get(location + "/metadata.xmp").status_code == 200
        assert '"reviewed_runs":1' in client.get("/api/evals/metrics").text
        assert client.get("/evals.jsonl").text.count("\n") == 1
        assert client.post(location + "/rerun", follow_redirects=False).status_code == 303


def test_invalid_requests_and_cross_origin_are_rejected(settings):
    with TestClient(create_app(settings)) as client:
        assert client.get("/jobs/missing").status_code == 404
        assert client.post("/imports", files={"image": ("bad.jpg", b"bad")}).status_code == 422
        assert (
            client.post("/imports", headers={"Origin": "https://evil.example"}).status_code == 403
        )
        assert client.get("/", headers={"Host": "evil.example"}).status_code == 400


def test_html_escapes_untrusted_context(settings, repository, job_id):
    with repository.connect() as db:
        db.execute("UPDATE assets SET filename=?", ("<script>alert(1)</script>.jpg",))
    with TestClient(create_app(settings)) as client:
        page = client.get("/jobs/" + job_id).text
        assert "<script>alert(1)</script>" not in page
        assert "&lt;script&gt;" in page
