"""Browsers get a page, machines get JSON."""

HTML = {"accept": "text/html,application/xhtml+xml"}


def test_a_missing_page_renders_html_for_a_browser(signed_in):
    response = signed_in.get("/sites/nobody-owns-this.example", headers=HTML)

    assert response.status_code == 404
    assert response.headers["content-type"].startswith("text/html")
    assert "Nothing here" in response.text


def test_the_same_route_answers_json_for_the_api(signed_in):
    response = signed_in.get("/api/stats/nobody-owns-this.example/summary", headers=HTML)

    assert response.status_code == 404
    assert response.json() == {"detail": "No such site"}


def test_clients_that_do_not_want_html_still_get_json(signed_in):
    response = signed_in.get(
        "/sites/nobody-owns-this.example", headers={"accept": "application/json"}
    )

    assert response.status_code == 404
    assert response.json() == {"detail": "No such site"}


def test_an_unauthenticated_form_post_renders_a_page(client):
    response = client.post("/sites", data={"domain": "x.example"}, headers=HTML)

    assert response.status_code == 401
    assert "not signed in" in response.text.lower()
