from __future__ import annotations

import os
from pathlib import Path

from flask import Flask, flash, redirect, render_template, request, url_for

from .core import HockeySchedulingApp


def create_app(db_path: str | None = None) -> Flask:
    app = Flask(
        __name__,
        template_folder=str(Path(__file__).resolve().parent / "templates"),
        static_folder=str(Path(__file__).resolve().parent / "static"),
    )
    app.config["SECRET_KEY"] = "dev-secret-key"
    app.config["DATABASE"] = db_path or os.getenv("HOCKEYBEMANNING_DB", "hockeybemanning.db")
    app.config["SCHEDULER"] = HockeySchedulingApp(app.config["DATABASE"])

    @app.get("/")
    def dashboard() -> str:
        scheduler = app.config["SCHEDULER"]
        teams = scheduler.connection.execute(
            "SELECT id, name FROM teams ORDER BY name"
        ).fetchall()
        return render_template("dashboard.html", teams=teams)

    @app.get("/players")
    def players_page() -> str:
        scheduler = app.config["SCHEDULER"]
        players = scheduler.all_players()
        return render_template("players.html", players=players)

    @app.post("/players/new")
    def create_player() -> object:
        name = (request.form.get("name") or "").strip()
        ovr = request.form.get("ovr") == "on"
        cafe = request.form.get("cafe") == "on"
        if name:
            app.config["SCHEDULER"].add_player(name, ovr=ovr, cafe=cafe)
        return redirect(url_for("players_page"))

    @app.post("/teams/new")
    def create_team() -> object:
        name = (request.form.get("name") or "").strip()
        if name:
            app.config["SCHEDULER"].create_team(name)
        return redirect(url_for("dashboard"))

    @app.route("/teams/<int:team_id>", methods=["GET", "POST"])
    def team_page(team_id: int) -> str | object:
        scheduler = app.config["SCHEDULER"]
        team = scheduler.connection.execute(
            "SELECT id, name FROM teams WHERE id = ?",
            (team_id,),
        ).fetchone()
        if team is None:
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            selected_ids = [int(value) for value in request.form.getlist("team_player_ids") if value]
            if selected_ids or request.form.getlist("team_player_ids") == []:
                scheduler.set_team_players(team_id, selected_ids)

            name = (request.form.get("name") or "").strip()
            ovr = request.form.get("ovr") == "on"
            cafe = request.form.get("cafe") == "on"
            if name:
                player_id = scheduler.add_player(name, ovr=ovr, cafe=cafe)
                scheduler.assign_player_to_team(team_id, player_id)

        team_events = scheduler.connection.execute(
            "SELECT id, name FROM events WHERE team_id = ? ORDER BY id DESC",
            (team_id,),
        ).fetchall()
        assigned_players = scheduler.team_players(team_id)
        team_player_stats = {stat["player_id"]: stat for stat in scheduler.team_player_stats(team_id)}
        return render_template(
            "team.html",
            team=team,
            assigned_players=assigned_players,
            team_events=team_events,
            team_player_stats=team_player_stats,
        )

    @app.route("/teams/<int:team_id>/roster", methods=["GET", "POST"])
    def team_roster_page(team_id: int) -> str | object:
        scheduler = app.config["SCHEDULER"]
        team = scheduler.connection.execute(
            "SELECT id, name FROM teams WHERE id = ?",
            (team_id,),
        ).fetchone()
        if team is None:
            return redirect(url_for("dashboard"))

        if request.method == "POST":
            selected_ids = [int(value) for value in request.form.getlist("team_player_ids") if value]
            if selected_ids or request.form.getlist("team_player_ids") == []:
                scheduler.set_team_players(team_id, selected_ids)
                return redirect(url_for("team_roster_page", team_id=team_id))

            name = (request.form.get("name") or "").strip()
            ovr = request.form.get("ovr") == "on"
            cafe = request.form.get("cafe") == "on"
            if name:
                player_id = scheduler.add_player(name, ovr=ovr, cafe=cafe)
                scheduler.assign_player_to_team(team_id, player_id)
                return redirect(url_for("team_roster_page", team_id=team_id))

        assigned_player_ids = {player["player_id"] for player in scheduler.team_players(team_id)}
        team_player_stats = {stat["player_id"]: stat for stat in scheduler.team_player_stats(team_id)}
        return render_template(
            "team_roster.html",
            team=team,
            all_players=scheduler.all_players(),
            assigned_player_ids=assigned_player_ids,
            assigned_players=scheduler.team_players(team_id),
            team_player_stats=team_player_stats,
        )

    @app.route("/players/<int:player_id>", methods=["POST"])
    def update_player(player_id: int) -> object:
        scheduler = app.config["SCHEDULER"]
        name = (request.form.get("name") or "").strip()
        ovr = request.form.get("ovr") == "on"
        cafe = request.form.get("cafe") == "on"
        if name:
            scheduler.update_player(player_id, name=name, ovr=ovr, cafe=cafe)
        return redirect(url_for("players_page"))

    @app.post("/players/<int:player_id>/delete")
    def delete_player(player_id: int) -> object:
        app.config["SCHEDULER"].delete_player(player_id)
        return redirect(url_for("players_page"))

    @app.post("/events/new")
    def create_event() -> object:
        team_id = request.form.get("team_id")
        name = (request.form.get("name") or "").strip()
        if team_id and name:
            event_id = app.config["SCHEDULER"].create_event(int(team_id), name)
            return redirect(url_for("event_page", event_id=event_id))
        return redirect(url_for("dashboard"))

    @app.route("/events/<int:event_id>", methods=["GET", "POST"])
    def event_page(event_id: int) -> str | object:
        scheduler = app.config["SCHEDULER"]
        event = scheduler.connection.execute(
            "SELECT id, team_id, name FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            return redirect(url_for("dashboard"))

        team_players = scheduler.team_players(event["team_id"])
        selected_players = scheduler._selected_players(event_id)
        selected_official_ids = {player["player_id"] for player in selected_players if bool(player["official"])}
        assignments = (
            scheduler.get_event_assignments(event_id)
            if selected_players
            else {"off_ice": [], "cafe": []}
        )
        preview_assignments = None
        randomize = False
        exclude_ids: set[int] = set()

        if request.method == "POST":
            try:
                selected_ids = [int(value) for value in request.form.getlist("player_ids") if value]
                official_ids = [int(value) for value in request.form.getlist("official_ids") if value]
                exclude_ids = {int(value) for value in request.form.getlist("exclude_ids") if value}
                randomize = request.form.get("randomize") == "on"
                action = request.form.get("action")

                if action == "confirm":
                    if not selected_ids:
                        raise ValueError("Select at least one player before confirming the assignment.")
                    preview_assignments = scheduler.preview_assignments(
                        event_id,
                        selected_player_ids=selected_ids,
                        official_ids=official_ids,
                        exclude_player_ids=exclude_ids,
                        randomize=randomize,
                    )
                    scheduler.select_players_for_event(event_id, selected_ids, official_ids=official_ids)
                    scheduler.confirm_assignments(event_id, assignments=preview_assignments)
                    return redirect(url_for("event_page", event_id=event_id))

                if action == "assign":
                    if not selected_ids:
                        raise ValueError("Select at least one player before assigning duties.")
                    preview_assignments = scheduler.preview_assignments(
                        event_id,
                        selected_player_ids=selected_ids,
                        official_ids=official_ids,
                        exclude_player_ids=exclude_ids,
                        randomize=randomize,
                    )
                    selected_players = [
                        player
                        for player in team_players
                        if int(player["player_id"]) in selected_ids
                    ]
                    selected_official_ids = set(official_ids)
                    assignments = preview_assignments
                    return render_template(
                        "event.html",
                        event=event,
                        players=team_players,
                        selected_players=selected_players,
                        assignments=assignments,
                        selected_official_ids=selected_official_ids,
                        preview_assignments=preview_assignments,
                        randomize=randomize,
                        exclude_ids=exclude_ids,
                        selected_player_ids=selected_ids,
                    )

                if action == "manual":
                    off_ice_ids = [int(value) for value in request.form.getlist("off_ice") if value]
                    cafe_ids = [int(value) for value in request.form.getlist("cafe") if value]
                    scheduler.override_assignments(
                        event_id,
                        off_ice_player_ids=off_ice_ids,
                        cafe_player_ids=cafe_ids,
                    )
                    return redirect(url_for("event_page", event_id=event_id))
            except ValueError as exc:
                flash(str(exc))
                return redirect(url_for("event_page", event_id=event_id))

        return render_template(
            "event.html",
            event=event,
            players=team_players,
            selected_players=selected_players,
            assignments=assignments,
            selected_official_ids=selected_official_ids,
            preview_assignments=preview_assignments,
            randomize=randomize,
            exclude_ids=exclude_ids,
            selected_player_ids=[int(player["player_id"]) for player in selected_players],
        )

    @app.get("/history")
    def history() -> str:
        scheduler = app.config["SCHEDULER"]
        events = scheduler.connection.execute(
            "SELECT id, team_id, name FROM events ORDER BY id DESC"
        ).fetchall()
        return render_template("history.html", events=events)

    return app


app = create_app()


if __name__ == "__main__":
    app.run(debug=True)
