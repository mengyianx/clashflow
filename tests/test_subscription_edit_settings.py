"""Ticket #13: Subscription-derived edits — overwrite warning and fetch settings.

Assertions stay at the Management Panel HTTP API seam (`/api/panel/configs*`
and `/api/panel/subscriptions*`): the Editor's overwrite warning keys off the
seam-visible `kind` on the Config read side, the one-step copy flow is the
single `POST /api/panel/configs/{name}/copy` call (#11's endpoint), and the
fetch settings (custom user agent, update-interval override) are observed
through the recorded request headers of a local provider origin and through
the Subscription entries. The pinned Kernel is a real fixture; runs are
deterministic and offline. UI rendering of the warning and the copy button is
covered by a real-browser check (see the ticket report), not here.
"""

from conftest import origin_server

SUB_V1 = b"mixed-port: 7892\nallow-lan: true\n"
SUB_V2 = b"mixed-port: 7893\nallow-lan: true\n"
DEFAULT_USER_AGENT = "ClashFlow/0.1"


async def _add_subscription(panel, name: str, url: str):
    return await panel.client.post("/api/panel/subscriptions", json={"name": name, "url": url})


async def test_opening_the_editor_for_a_subscription_derived_config_marks_it_for_the_overwrite_warning(
    boot_app,
) -> None:
    # AC1 (seam half): the Editor read side exposes `kind`, the seam-visible
    # marking the overwrite warning keys off; a Hand-written Config carries
    # kind=hand-written, so the warning must not render for it.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
        derived = (await panel.client.get("/api/panel/configs/prov")).json()
        assert derived["kind"] == "subscription-derived"
        assert derived["content"] == SUB_V1.decode()

        handwritten = (await panel.client.get("/api/panel/configs/default")).json()
        assert handwritten["kind"] == "hand-written"


async def test_the_editor_copy_to_hand_written_flow_creates_an_independent_config_in_one_call(
    boot_app,
) -> None:
    # AC2 (seam half): ONE `POST /api/panel/configs/{name}/copy` call turns a
    # Subscription-derived Config into an independent Hand-written Config;
    # the next refresh rewrites only the Subscription-derived Config and
    # never touches the copy.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            copy = await panel.client.post(
                "/api/panel/configs/prov/copy", json={"name": "prov-copy"}
            )
            assert copy.status_code == 201
            assert copy.json()["kind"] == "hand-written"

            origin.state["body"] = SUB_V2
            refresh = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert refresh.status_code == 200

        assert (await panel.client.get("/api/panel/configs/prov")).json()["content"] == (
            SUB_V2.decode()
        )
        copied = (await panel.client.get("/api/panel/configs/prov-copy")).json()
        assert copied["kind"] == "hand-written"
        assert copied["content"] == SUB_V1.decode()


async def test_a_custom_user_agent_is_sent_on_that_subscriptions_fetches_and_the_default_applies_when_unset(
    boot_app,
) -> None:
    # AC3: a custom user agent set through the settings endpoint is the
    # User-Agent header the provider receives on that Subscription's next
    # fetch; without one, the documented default ClashFlow/0.1 is sent.
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201
            assert origin.state["user_agents"] == [DEFAULT_USER_AGENT]

            patched = await panel.client.patch(
                "/api/panel/subscriptions/prov", json={"user_agent": "ClashFlow-Test/9.9"}
            )
            assert patched.status_code == 200
            assert patched.json()["user_agent"] == "ClashFlow-Test/9.9"

            refresh = await panel.client.post("/api/panel/subscriptions/prov/refresh")
            assert refresh.status_code == 200
            assert origin.state["user_agents"][-1] == "ClashFlow-Test/9.9"

            # Fetch-unsafe or empty user agents are refused, the stored one
            # stays in effect.
            for bad in ("bad\nua", "bad\rua", "   "):
                resp = await panel.client.patch(
                    "/api/panel/subscriptions/prov", json={"user_agent": bad}
                )
                assert resp.status_code == 400

        # A Subscription without a custom UA keeps sending the default on
        # every fetch, not only the first.
        with origin_server(SUB_V1) as plain:
            assert (await _add_subscription(panel, "plain", plain.url)).status_code == 201
            assert (
                await panel.client.post("/api/panel/subscriptions/plain/refresh")
            ).status_code == 200
            assert plain.state["user_agents"] == [DEFAULT_USER_AGENT, DEFAULT_USER_AGENT]


async def test_the_update_interval_override_round_trips_through_the_subscription_api(
    boot_app,
) -> None:
    # AC4 (storage + API exposure half; the scheduler consuming it is #16's
    # scope): the per-Subscription override is stored as a positive number of
    # MINUTES, round-trips through get + list under the documented field name
    # `update_interval_minutes`, and NULL means "use the global default".
    async with boot_app() as panel:
        with origin_server(SUB_V1) as origin:
            assert (await _add_subscription(panel, "prov", origin.url)).status_code == 201

        entry = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert entry["name"] == "prov"
        assert entry["update_interval_minutes"] is None
        assert entry["user_agent"] is None

        patched = await panel.client.patch(
            "/api/panel/subscriptions/prov", json={"update_interval_minutes": 720}
        )
        assert patched.status_code == 200
        assert patched.json()["update_interval_minutes"] == 720
        listed = (await panel.client.get("/api/panel/subscriptions")).json()[0]
        assert listed["update_interval_minutes"] == 720

        # Partial update: a user-agent-only patch leaves the interval alone
        # (and vice versa the other way around).
        ua_only = await panel.client.patch(
            "/api/panel/subscriptions/prov", json={"user_agent": "ClashFlow-Test/9.9"}
        )
        assert ua_only.json()["update_interval_minutes"] == 720
        assert ua_only.json()["user_agent"] == "ClashFlow-Test/9.9"
        interval_only = await panel.client.patch(
            "/api/panel/subscriptions/prov", json={"update_interval_minutes": 90}
        )
        assert interval_only.json()["user_agent"] == "ClashFlow-Test/9.9"

        # The override must be a positive number of minutes.
        for invalid in (0, -5):
            resp = await panel.client.patch(
                "/api/panel/subscriptions/prov", json={"update_interval_minutes": invalid}
            )
            assert resp.status_code == 400

        # Explicit null clears the override back to the global default.
        cleared = await panel.client.patch(
            "/api/panel/subscriptions/prov", json={"update_interval_minutes": None}
        )
        assert cleared.status_code == 200
        assert cleared.json()["update_interval_minutes"] is None
        assert cleared.json()["user_agent"] == "ClashFlow-Test/9.9"

        missing = await panel.client.patch(
            "/api/panel/subscriptions/nope", json={"update_interval_minutes": 720}
        )
        assert missing.status_code == 404
