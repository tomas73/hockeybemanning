from concurrent.futures import ThreadPoolExecutor

from hockeybemanning.web import create_app


def test_create_app_exposes_dashboard_route() -> None:
    db_path = ":memory:"
    app = create_app(db_path=db_path)
    client = app.test_client()

    response = client.get("/")

    assert response.status_code == 200
    assert b"Dashboard" in response.data


def test_event_page_renders_selected_players() -> None:
    app = create_app(db_path=":memory:")
    client = app.test_client()
    scheduler = app.config["SCHEDULER"]

    team_id = scheduler.create_team("U12")
    player_id = scheduler.add_player(team_id, "Alice")
    event_id = scheduler.create_event(team_id, "Match 1")
    scheduler.select_players_for_event(event_id, [player_id])

    response = client.get(f"/events/{event_id}")

    assert response.status_code == 200
    assert b"Match 1" in response.data
    assert b"Alice" in response.data


def test_event_page_handles_invalid_assignment_request() -> None:
    app = create_app(db_path=":memory:")
    client = app.test_client()
    scheduler = app.config["SCHEDULER"]

    team_id = scheduler.create_team("U12")
    for idx in range(1, 8):
        scheduler.add_player(team_id, f"Player {idx}")
    event_id = scheduler.create_event(team_id, "Match 1")

    response = client.post(f"/events/{event_id}", data={"action": "assign"})

    assert response.status_code == 302
    assert response.headers["Location"] == f"/events/{event_id}"


def test_player_form_accepts_ovr_flag() -> None:
    app = create_app(db_path=":memory:")
    client = app.test_client()
    scheduler = app.config["SCHEDULER"]

    team_id = scheduler.create_team("U12")
    response = client.post(f"/teams/{team_id}", data={"name": "Alice", "ovr": "on"})

    assert response.status_code == 200
    assert scheduler.connection.execute("SELECT COUNT(*) FROM players WHERE ovr = 1").fetchone()[0] == 1


def test_scheduler_supports_multi_thread_use(tmp_path) -> None:
    db_path = tmp_path / "app.db"
    app = create_app(db_path=str(db_path))
    scheduler = app.config["SCHEDULER"]

    def create_team(name: str) -> None:
        scheduler.create_team(name)

    with ThreadPoolExecutor(max_workers=3) as executor:
        list(executor.map(create_team, ["A", "B", "C"]))

    assert scheduler.connection.execute("SELECT COUNT(*) FROM teams").fetchone()[0] == 3
