import datetime as dt
import re
from pathlib import Path

from app.models import Event
from app.routers import dashboard
from app.services import accounts
from tests.conftest import OWNER_PASSWORD, SITE_DOMAIN, with_local_bucket


def add_event(db, **overrides):
    defaults = {
        "site_id": SITE_DOMAIN,
        "visitor_id": "visitor-1",
        "pathname": "/products/blue-mug",
        "timestamp": dt.datetime.now(dt.UTC),
        "name": "pageview",
        "source": "Hacker News",
        "browser": "Firefox",
        "os": "Linux",
        "device": "desktop",
        "country": "DE",
        "screen": "Desktop",
    }
    db.add(Event(**with_local_bucket(defaults | overrides)))
    db.commit()


def test_dashboard_renders_the_headline_numbers(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="b")

    rebuild_rollups()

    response = signed_in.get(f"/sites/{SITE_DOMAIN}")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/html")
    assert SITE_DOMAIN in response.text
    assert "Visitors" in response.text
    assert "Pageviews" in response.text


def test_dashboard_renders_each_breakdown_panel(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Pages" in body
    assert "/products/blue-mug" in body
    assert "Hacker News" in body
    assert "desktop" in body
    # Every dimension gets a tab, not just the four that used to fit.
    for tab in ("Sources", "Countries", "Devices", "Browsers", "Systems"):
        assert tab in body


def test_dashboard_draws_a_chart(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "<svg" in body
    assert 'class="chart-curve"' in body
    # Sparklines in the headline tiles.
    assert 'class="spark"' in body


def test_the_selected_period_is_marked_current(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}", params={"period": "7d"}).text

    assert f'href="/sites/{SITE_DOMAIN}?period=7d"\n       class="current"' in body


def test_a_site_with_no_traffic_says_so(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "No visitors in this period yet." in body


def test_signed_out_visitors_are_sent_to_the_login_page(client, site):
    response = client.get(f"/sites/{SITE_DOMAIN}", follow_redirects=False)

    assert response.status_code == 303
    assert response.headers["location"] == "/login"


def test_another_persons_dashboard_is_a_404(signed_in, db_session, rebuild_rollups):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="not-yours.example")

    rebuild_rollups()

    assert signed_in.get("/sites/not-yours.example").status_code == 404


def test_an_invalid_period_is_rejected(signed_in, site):
    assert signed_in.get(f"/sites/{SITE_DOMAIN}", params={"period": "forever"}).status_code == 422


def test_index_lists_only_your_own_sites(signed_in, db_session, rebuild_rollups, site, account):
    accounts.add_site(db_session, owner=account, domain="second.example")
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    rebuild_rollups()

    body = signed_in.get("/").text

    assert f"/sites/{SITE_DOMAIN}" in body
    assert "/sites/second.example" in body
    assert "theirs.example" not in body


def test_index_prompts_for_a_first_site(signed_in):
    body = signed_in.get("/").text

    assert "No sites yet" in body
    assert 'name="domain"' in body


def test_the_front_page_explains_itself_to_signed_out_visitors(client):
    """A portfolio link should not open onto a bare login box."""
    response = client.get("/")

    assert response.status_code == 200
    assert "Know what your visitors read" in response.text
    assert "/signup" in response.text


def test_a_published_dashboard_is_readable_by_anyone(
    client, db_session, site, rebuild_rollups
):
    """The point of publishing: a link somebody can follow without signing up."""
    add_event(db_session, visitor_id="a")
    accounts.set_visibility(db_session, site=site, public=True)
    rebuild_rollups()

    response = client.get(f"/sites/{SITE_DOMAIN}")

    assert response.status_code == 200
    assert "Visitors" in response.text
    assert "Public" in response.text


def test_a_visitor_to_a_published_dashboard_gets_no_controls(client, db_session, site):
    accounts.set_visibility(db_session, site=site, public=True)

    body = client.get(f"/sites/{SITE_DOMAIN}").text

    assert "Make private" not in body
    assert "Publish this dashboard" not in body


def test_the_owner_sees_the_publish_control(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Publish this dashboard" in body
    assert "Only you can see this." in body


def test_the_owner_can_publish_and_unpublish(signed_in, db_session, site):
    published = signed_in.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "true"}, follow_redirects=False
    )

    assert published.status_code == 303
    db_session.refresh(site)
    assert site.public is True

    signed_in.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "false"}, follow_redirects=False
    )
    db_session.refresh(site)
    assert site.public is False


def test_a_stranger_cannot_publish_somebody_elses_site(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    theirs = accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    response = signed_in.post(
        "/sites/theirs.example/visibility", data={"public": "true"}, follow_redirects=False
    )

    assert response.status_code == 404
    db_session.refresh(theirs)
    assert theirs.public is False


def test_publishing_requires_an_account(client, site):
    assert client.post(
        f"/sites/{SITE_DOMAIN}/visibility", data={"public": "true"}
    ).status_code == 401


def test_the_goals_panel_explains_itself_when_empty(signed_in, site):
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Goals" in body
    assert "No custom events yet" in body


def test_the_owner_can_set_the_timezone(signed_in, db_session, site):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/timezone",
        data={"timezone": "Europe/Berlin"},
        follow_redirects=False,
    )

    assert response.status_code == 303
    db_session.refresh(site)
    assert site.timezone == "Europe/Berlin"


def test_an_invented_timezone_is_refused(signed_in, db_session, site):
    response = signed_in.post(
        f"/sites/{SITE_DOMAIN}/timezone",
        data={"timezone": "Mars/Olympus_Mons"},
        follow_redirects=False,
    )

    assert response.status_code == 422
    db_session.refresh(site)
    assert site.timezone == "UTC"


def test_a_stranger_cannot_set_somebody_elses_timezone(signed_in, db_session):
    stranger = accounts.register(db_session, email="stranger@example.com", password=OWNER_PASSWORD)
    theirs = accounts.add_site(db_session, owner=stranger, domain="theirs.example")

    response = signed_in.post(
        "/sites/theirs.example/timezone",
        data={"timezone": "Asia/Tokyo"},
        follow_redirects=False,
    )

    assert response.status_code == 404
    db_session.refresh(theirs)
    assert theirs.timezone == "UTC"


def test_the_dashboard_says_which_clock_it_is_using(signed_in, db_session, site):
    accounts.set_timezone(db_session, site=site, timezone="Asia/Tokyo")

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "days here start at midnight in Asia/Tokyo" in body


def test_dashboard_shows_the_bounce_rate(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a")
    add_event(db_session, visitor_id="a", pathname="/about")
    add_event(db_session, visitor_id="b")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Bounce rate" in body
    # One of two visitors read a single page and went no further.
    assert "50.0%" in body


def test_dashboard_has_entry_and_exit_panels(signed_in, db_session, rebuild_rollups, site):
    add_event(db_session, visitor_id="a", pathname="/landing")
    add_event(db_session, visitor_id="a", pathname="/goodbye")

    rebuild_rollups()

    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text

    assert "Entry pages" in body
    assert "Exit pages" in body
    assert "/landing" in body
    assert "/goodbye" in body


def test_every_tab_input_is_immediately_followed_by_its_panel(signed_in, db_session, site):
    """The invariant the stylesheet's one visibility rule depends on.

    `.tab-input:checked + .tab-panel` only works while each input sits directly
    before the panel it controls. The version this replaced needed a selector
    per tab and fell two behind, so the last two tabs opened onto nothing --
    invisible to every test, because all panels are in the document regardless.
    """
    body = signed_in.get(f"/sites/{SITE_DOMAIN}").text
    tabs = body.count('class="tab-input"')
    paired = len(re.findall(r'class="tab-input"[^>]*/>\s*<div class="tab-panel">', body))

    assert tabs == len(dashboard.PANELS)
    assert paired == tabs, "an input is not directly followed by its panel"


def test_the_stylesheet_can_highlight_every_tab():
    """Panel visibility no longer counts tabs, but the active label still must.

    CSS cannot match a label to a checked input by shared name, so the
    highlight is correlated by position and has an upper bound.
    """
    css = (Path(__file__).parent.parent / "static" / "dashboard.css").read_text(encoding="utf-8")
    highest = max(int(n) for n in re.findall(r"nth-of-type\((\d+)\):checked", css))

    assert highest >= len(dashboard.PANELS)


def test_the_landing_page_carries_the_animated_scene(client):
    """The lighthouse on the cover, and the terms it exists under.

    It is decorative, so it must be hidden from screen readers; and it is
    built from CSS transforms alone, because the content security policy
    forbids outside libraries and the page promises to work without script.
    """
    body = client.get("/").text

    assert 'class="hero-scene" aria-hidden="true"' in body
    # Four surfaces of revolution, 36 facets each: the rock it stands on, the
    # tapered tower, the lantern glass, and the cone of the roof.
    for surface in ("plinth-facet", "tower-facet", "lantern-facet", "roof-facet"):
        assert body.count(f'class="{surface}"') == 36, surface
    # Twelve uprights round the gallery, which is what crosses in front of and
    # behind the lantern as the tower turns.
    assert body.count('class="rail-post"') == 12
    assert 'class="lamp"' in body
    assert body.count('class="mote"') == 7


def test_the_tapered_solids_are_built_as_frustums_not_cylinders():
    """The difference between a cone and a cylinder wearing a hat.

    A tapered surface of revolution needs three things agreeing with each
    other: a trapezoid clip, a facet height measured along the slant rather
    than the vertical, and a rotateX of the slant angle. Getting the tilt
    without the clip, or either without the other, is what makes a model read
    as a drawing of itself -- the roof was a single flat triangle facing the
    viewer before this.
    """
    css = (Path(__file__).parent.parent / "static" / "dashboard.css").read_text(encoding="utf-8")

    for facet, tilt in (("tower-facet", "4.899deg"), ("roof-facet", "53.13deg"),
                        ("plinth-facet", "18.435deg")):
        rule = css[css.index(f".{facet} {{") :].split("}", 1)[0]
        assert "clip-path: polygon(" in rule, f"{facet} tapers but is not clipped"
        assert f"rotateX({tilt})" in rule, f"{facet} is not tilted to its slant"

    # The lantern is the one straight cylinder, so it has neither.
    lantern = css[css.index(".lantern-facet {") :].split("}", 1)[0]
    assert "clip-path" not in lantern and "rotateX" not in lantern


def test_the_cover_turns_the_light_and_not_the_tower(client):
    """The reason this is a lighthouse rather than a product on a turntable.

    A beacon is a light that goes round, so the one piece of motion on the page
    describes what the thing is called after. A tower that spun would be a lie
    about the object, so it only sways far enough to show it is round.
    """
    body = client.get("/").text
    css = (Path(__file__).parent.parent / "static" / "dashboard.css").read_text(encoding="utf-8")

    assert body.count('class="beam"') + body.count('class="beam beam-back"') == 2

    sway = css[css.index(".beacon-sway {") :].split("}", 1)[0]
    assert "beacon-sway" in sway, "the tower should sway rather than turn"

    turn = css[css.index("@keyframes beacon-sway") :].split("}\n}", 1)[0]
    assert "360deg" not in turn, "the tower must not make a full revolution"
    sweep = css[css.index("@keyframes beam-sweep") :].split("}\n}", 1)[0]
    assert "360deg" in sweep, "the beam should be the thing that goes all the way round"


def test_the_scene_stands_still_for_reduced_motion():
    """A spinning object is exactly what that preference exists to stop.

    This used to assert the block contained `.can-spin { animation: none;` and
    three more like it. Every one of those strings was present and none of them
    did anything, which is how the bug shipped under a green suite -- the test
    checked that the declaration had been written, not that it survived the
    cascade. It now checks the properties that decide the outcome.

    Matching everything rather than a list of classes is the point: the list
    fell behind the moment the cover page arrived with five animations, and a
    universal selector needs nobody to remember anything.
    """
    css = (Path(__file__).parent.parent / "static" / "dashboard.css").read_text(encoding="utf-8")
    reduced = css[css.index("@media (prefers-reduced-motion: reduce)") :]

    assert "*::before" in reduced and "*::after" in reduced
    assert "animation-duration: 0.01ms !important" in reduced
    assert "animation-iteration-count: 1 !important" in reduced
    # A collapsed animation still applies its first keyframe, and an animation
    # outranks a normal declaration -- so a resting pose has to be important or
    # the tower sits square-on and the beam parks pointing flat right.
    assert "transform: rotateY(-9deg) !important" in reduced


def test_reduced_motion_is_declared_after_the_animations_it_cancels():
    """The cascade rule this block's whole effect depends on.

    A media query carries no specificity of its own, so a rule inside one beats
    a rule outside it only by coming later in the file. This block used to sit
    near the top and name each animated class by hand, and every class it named
    was defined two hundred lines below -- so every cancellation lost the
    cascade and did nothing. Somebody who had asked their system for less
    motion still got a sweeping beam, a swaying tower and drifting motes;
    confirmed in a browser, where the animations still computed to their own
    names and kept running.

    pytest cannot see a cascade, so it asserts the position that decides it.
    """
    css = (Path(__file__).parent.parent / "static" / "dashboard.css").read_text(encoding="utf-8")
    # The comments quote CSS at itself, so strip them before hunting for
    # declarations or the prose trips the assertion.
    declarations = re.sub(r"/\*.*?\*/", "", css, flags=re.S)

    block = declarations.index("@media (prefers-reduced-motion: reduce)")

    assert "animation:" not in declarations[block:], (
        "an animation is declared after the reduced-motion block, so it wins the cascade"
    )
