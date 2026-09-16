from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from typing import Iterable

ROLE_OFF_ICE = "off_ice"
ROLE_CAFE = "cafe"
ROLE_ORDER = (ROLE_CAFE, ROLE_OFF_ICE)
ROLE_LIMITS = {
    ROLE_OFF_ICE: 5,
    ROLE_CAFE: 2,
}


@dataclass(frozen=True)
class Assignment:
    role: str
    player_id: int
    player_name: str
    parent_name: str
    is_manual: bool


class HockeySchedulingApp:
    def __init__(self, db_path: str = ":memory:") -> None:
        self.connection = sqlite3.connect(db_path)
        self.connection.row_factory = sqlite3.Row
        self._create_schema()

    def close(self) -> None:
        self.connection.close()

    def create_team(self, name: str) -> int:
        return self._insert_and_return_id(
            "INSERT INTO teams (name) VALUES (?)",
            (name,),
        )

    def add_player(self, team_id: int, name: str, parent_name: str) -> int:
        self._require_team(team_id)
        return self._insert_and_return_id(
            "INSERT INTO players (team_id, name, parent_name) VALUES (?, ?, ?)",
            (team_id, name, parent_name),
        )

    def create_event(self, team_id: int, name: str) -> int:
        self._require_team(team_id)
        return self._insert_and_return_id(
            "INSERT INTO events (team_id, name) VALUES (?, ?)",
            (team_id, name),
        )

    def select_players_for_event(self, event_id: int, player_ids: Iterable[int]) -> None:
        event = self._require_event(event_id)
        selected_ids = list(dict.fromkeys(player_ids))
        self.connection.execute("DELETE FROM event_players WHERE event_id = ?", (event_id,))
        self.connection.execute("DELETE FROM assignments WHERE event_id = ?", (event_id,))
        for player_id in selected_ids:
            player = self._require_player(player_id)
            if player["team_id"] != event["team_id"]:
                raise ValueError("Selected players must belong to the event team.")
            self.connection.execute(
                "INSERT INTO event_players (event_id, player_id) VALUES (?, ?)",
                (event_id, player_id),
            )
        self.connection.commit()

    def override_assignments(
        self,
        event_id: int,
        *,
        off_ice_player_ids: Iterable[int] = (),
        cafe_player_ids: Iterable[int] = (),
    ) -> dict[str, list[Assignment]]:
        self._require_event(event_id)
        manual_assignments = {
            ROLE_OFF_ICE: list(dict.fromkeys(off_ice_player_ids)),
            ROLE_CAFE: list(dict.fromkeys(cafe_player_ids)),
        }
        self.connection.execute(
            "DELETE FROM assignments WHERE event_id = ? AND is_manual = 1",
            (event_id,),
        )
        self._store_manual_assignments(event_id, manual_assignments)
        self.connection.commit()
        return self.assign_fairly(event_id)

    def assign_fairly(self, event_id: int) -> dict[str, list[Assignment]]:
        self._require_event(event_id)
        selected_players = self._selected_players(event_id)
        if not selected_players:
            raise ValueError("Select players for the event before assigning duties.")

        manual_assignments = self._manual_assignments(event_id)
        self._validate_assignments(event_id, manual_assignments)

        self.connection.execute(
            "DELETE FROM assignments WHERE event_id = ? AND is_manual = 0",
            (event_id,),
        )

        used_player_ids = {assignment["player_id"] for assignment in manual_assignments}
        used_parent_names = {assignment["parent_name"] for assignment in manual_assignments}

        remaining_players = [
            player
            for player in selected_players
            if player["player_id"] not in used_player_ids and player["parent_name"] not in used_parent_names
        ]

        for role in ROLE_ORDER:
            remaining_slots = ROLE_LIMITS[role] - sum(
                1 for assignment in manual_assignments if assignment["role"] == role
            )
            for _ in range(remaining_slots):
                candidate = self._choose_candidate(event_id, role, remaining_players)
                if candidate is None:
                    raise ValueError(
                        "Not enough distinct parents selected to cover cafe and off ice roles."
                    )
                self.connection.execute(
                    "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 0)",
                    (event_id, candidate["player_id"], role),
                )
                remaining_players = [
                    player
                    for player in remaining_players
                    if player["player_id"] != candidate["player_id"]
                    and player["parent_name"] != candidate["parent_name"]
                ]

        self.connection.commit()
        return self.get_event_assignments(event_id)

    def get_event_assignments(self, event_id: int) -> dict[str, list[Assignment]]:
        self._require_event(event_id)
        rows = self.connection.execute(
            """
            SELECT
                assignments.role,
                assignments.player_id,
                players.name AS player_name,
                players.parent_name,
                CAST(assignments.is_manual AS INTEGER) AS is_manual
            FROM assignments
            JOIN players ON players.id = assignments.player_id
            WHERE assignments.event_id = ?
            ORDER BY assignments.role, assignments.is_manual DESC, assignments.player_id
            """,
            (event_id,),
        ).fetchall()
        grouped = {ROLE_OFF_ICE: [], ROLE_CAFE: []}
        for row in rows:
            grouped[row["role"]].append(
                Assignment(
                    role=row["role"],
                    player_id=row["player_id"],
                    player_name=row["player_name"],
                    parent_name=row["parent_name"],
                    is_manual=bool(row["is_manual"]),
                )
            )
        return grouped

    def _store_manual_assignments(self, event_id: int, assignments: dict[str, list[int]]) -> None:
        selected_by_id = {player["player_id"]: player for player in self._selected_players(event_id)}
        used_player_ids: set[int] = set()
        used_parent_names: set[str] = set()
        for role, player_ids in assignments.items():
            if len(player_ids) > ROLE_LIMITS[role]:
                raise ValueError(f"Too many manual {role} assignments.")
            for player_id in player_ids:
                player = selected_by_id.get(player_id)
                if player is None:
                    raise ValueError("Manual overrides must reference selected event players.")
                if player_id in used_player_ids or player["parent_name"] in used_parent_names:
                    raise ValueError("A parent can only be assigned to one duty per event.")
                used_player_ids.add(player_id)
                used_parent_names.add(player["parent_name"])
                self.connection.execute(
                    "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 1)",
                    (event_id, player_id, role),
                )

    def _validate_assignments(self, event_id: int, assignments: list[sqlite3.Row]) -> None:
        selected_by_id = {player["player_id"]: player for player in self._selected_players(event_id)}
        used_player_ids: set[int] = set()
        used_parent_names: set[str] = set()
        for role in ROLE_LIMITS:
            role_assignments = [assignment for assignment in assignments if assignment["role"] == role]
            if len(role_assignments) > ROLE_LIMITS[role]:
                raise ValueError(f"Too many {role} assignments for the event.")
        for assignment in assignments:
            player = selected_by_id.get(assignment["player_id"])
            if player is None:
                raise ValueError("Assignments must reference selected event players.")
            if assignment["player_id"] in used_player_ids or assignment["parent_name"] in used_parent_names:
                raise ValueError("A parent can only be assigned to one duty per event.")
            used_player_ids.add(assignment["player_id"])
            used_parent_names.add(assignment["parent_name"])

    def _choose_candidate(self, event_id: int, role: str, players: list[sqlite3.Row]) -> sqlite3.Row | None:
        ranked_players = sorted(players, key=lambda player: self._fairness_score(event_id, role, player))
        return ranked_players[0] if ranked_players else None

    def _fairness_score(self, event_id: int, role: str, player: sqlite3.Row) -> tuple[int, int, int, int]:
        row = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN assignments.role = ? THEN 1 ELSE 0 END), 0) AS role_count,
                COUNT(*) AS total_count,
                COALESCE(MAX(assignments.event_id), 0) AS last_event_id
            FROM assignments
            JOIN players ON players.id = assignments.player_id
            WHERE assignments.event_id != ? AND players.parent_name = ?
            """,
            (role, event_id, player["parent_name"]),
        ).fetchone()
        return (row["role_count"], row["total_count"], row["last_event_id"], player["player_id"])

    def _manual_assignments(self, event_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT assignments.role, assignments.player_id, players.parent_name
            FROM assignments
            JOIN players ON players.id = assignments.player_id
            WHERE assignments.event_id = ? AND assignments.is_manual = 1
            ORDER BY assignments.role, assignments.player_id
            """,
            (event_id,),
        ).fetchall()

    def _selected_players(self, event_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT players.id AS player_id, players.team_id, players.name, players.parent_name
            FROM event_players
            JOIN players ON players.id = event_players.player_id
            WHERE event_players.event_id = ?
            ORDER BY players.id
            """,
            (event_id,),
        ).fetchall()

    def _require_team(self, team_id: int) -> sqlite3.Row:
        team = self.connection.execute("SELECT id, name FROM teams WHERE id = ?", (team_id,)).fetchone()
        if team is None:
            raise ValueError("Unknown team.")
        return team

    def _require_player(self, player_id: int) -> sqlite3.Row:
        player = self.connection.execute(
            "SELECT id, team_id, name, parent_name FROM players WHERE id = ?",
            (player_id,),
        ).fetchone()
        if player is None:
            raise ValueError("Unknown player.")
        return player

    def _require_event(self, event_id: int) -> sqlite3.Row:
        event = self.connection.execute(
            "SELECT id, team_id, name FROM events WHERE id = ?",
            (event_id,),
        ).fetchone()
        if event is None:
            raise ValueError("Unknown event.")
        return event

    def _insert_and_return_id(self, query: str, parameters: tuple[object, ...]) -> int:
        cursor = self.connection.execute(query, parameters)
        self.connection.commit()
        return int(cursor.lastrowid)

    def _create_schema(self) -> None:
        self.connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS teams (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                name TEXT NOT NULL UNIQUE
            );

            CREATE TABLE IF NOT EXISTS players (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                parent_name TEXT NOT NULL,
                FOREIGN KEY (team_id) REFERENCES teams (id)
            );

            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                team_id INTEGER NOT NULL,
                name TEXT NOT NULL,
                FOREIGN KEY (team_id) REFERENCES teams (id)
            );

            CREATE TABLE IF NOT EXISTS event_players (
                event_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                PRIMARY KEY (event_id, player_id),
                FOREIGN KEY (event_id) REFERENCES events (id),
                FOREIGN KEY (player_id) REFERENCES players (id)
            );

            CREATE TABLE IF NOT EXISTS assignments (
                event_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                role TEXT NOT NULL CHECK (role IN ('off_ice', 'cafe')),
                is_manual INTEGER NOT NULL DEFAULT 0 CHECK (is_manual IN (0, 1)),
                PRIMARY KEY (event_id, player_id),
                FOREIGN KEY (event_id) REFERENCES events (id),
                FOREIGN KEY (player_id) REFERENCES players (id)
            );
            """
        )
        self.connection.commit()
