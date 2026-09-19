from __future__ import annotations

import random
import sqlite3
import threading
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
    is_manual: bool


class HockeySchedulingApp:
    def __init__(self, db_path: str = ":memory:") -> None:
        self._db_path = db_path
        self._local = threading.local()
        self._create_schema()

    @property
    def connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "connection"):
            connection = sqlite3.connect(self._db_path, check_same_thread=False)
            connection.row_factory = sqlite3.Row
            self._local.connection = connection
        return self._local.connection

    def close(self) -> None:
        connection = getattr(self._local, "connection", None)
        if connection is not None:
            connection.close()
            del self._local.connection

    def create_team(self, name: str) -> int:
        return self._insert_and_return_id(
            "INSERT INTO teams (name) VALUES (?)",
            (name,),
        )

    def add_player(self, team_id_or_name: int | str, name: str | None = None, ovr: bool = False, cafe: bool = False) -> int:
        if name is None:
            if not isinstance(team_id_or_name, str):
                raise TypeError("add_player requires a player name or a team_id plus name")
            name = team_id_or_name
            team_id = None
        else:
            team_id = int(team_id_or_name)

        player_id = self._insert_and_return_id(
            "INSERT INTO players (name, ovr, cafe) VALUES (?, ?, ?)",
            (name.strip(), int(bool(ovr)), int(bool(cafe))),
        )

        if team_id is not None:
            self.assign_player_to_team(team_id, player_id)
        return player_id

    def assign_player_to_team(self, team_id: int, player_id: int) -> None:
        self._require_team(team_id)
        self._require_player(player_id)
        existing = self.connection.execute(
            "SELECT 1 FROM team_players WHERE team_id = ? AND player_id = ?",
            (team_id, player_id),
        ).fetchone()
        if existing is None:
            self.connection.execute(
                "INSERT INTO team_players (team_id, player_id) VALUES (?, ?)",
                (team_id, player_id),
            )
            self.connection.commit()

    def set_team_players(self, team_id: int, player_ids: Iterable[int]) -> None:
        self._require_team(team_id)
        normalized_ids = list(dict.fromkeys(int(player_id) for player_id in player_ids if player_id))
        for player_id in normalized_ids:
            self._require_player(player_id)
        self.connection.execute("DELETE FROM team_players WHERE team_id = ?", (team_id,))
        for player_id in normalized_ids:
            self.connection.execute(
                "INSERT INTO team_players (team_id, player_id) VALUES (?, ?)",
                (team_id, player_id),
            )
        self.connection.commit()

    def all_players(self) -> list[sqlite3.Row]:
        return self.connection.execute(
            "SELECT id AS player_id, name, ovr, cafe FROM players ORDER BY name, id"
        ).fetchall()

    def team_players(self, team_id: int) -> list[sqlite3.Row]:
        self._require_team(team_id)
        return self.connection.execute(
            """
            SELECT players.id AS player_id, players.name, players.ovr, players.cafe
            FROM team_players
            JOIN players ON players.id = team_players.player_id
            WHERE team_players.team_id = ?
            ORDER BY players.name, players.id
            """,
            (team_id,),
        ).fetchall()

    def team_player_stats(self, team_id: int) -> list[dict[str, int | str]]:
        self._require_team(team_id)
        total_events = self.connection.execute(
            """
            SELECT COUNT(*) AS total_events
            FROM events
            WHERE team_id = ?
              AND EXISTS (
                  SELECT 1 FROM assignments WHERE assignments.event_id = events.id
              )
            """,
            (team_id,),
        ).fetchone()["total_events"]
        stats: list[dict[str, int | str]] = []
        for player in self.team_players(team_id):
            player_id = int(player["player_id"])
            row = self.connection.execute(
                """
                SELECT
                    (
                        SELECT COUNT(*)
                        FROM assignments
                        JOIN events ON events.id = assignments.event_id
                        WHERE assignments.player_id = ? AND events.team_id = ? AND assignments.role = 'off_ice'
                    ) AS off_ice_count,
                    (
                        SELECT COUNT(*)
                        FROM assignments
                        JOIN events ON events.id = assignments.event_id
                        WHERE assignments.player_id = ? AND events.team_id = ? AND assignments.role = 'cafe'
                    ) AS cafe_count,
                    (
                        SELECT COUNT(*)
                        FROM event_players
                        JOIN events ON events.id = event_players.event_id
                        WHERE event_players.player_id = ? AND events.team_id = ? AND event_players.official = 1
                          AND EXISTS (
                              SELECT 1 FROM assignments WHERE assignments.event_id = events.id
                          )
                    ) AS official_count,
                    (
                        SELECT COUNT(*)
                        FROM event_players
                        JOIN events ON events.id = event_players.event_id
                        WHERE event_players.player_id = ? AND events.team_id = ?
                          AND EXISTS (
                              SELECT 1 FROM assignments WHERE assignments.event_id = events.id
                          )
                    ) AS selected_count
                """,
                (player_id, team_id, player_id, team_id, player_id, team_id, player_id, team_id),
            ).fetchone()
            selected_count = int(row["selected_count"])
            stats.append(
                {
                    "player_id": player_id,
                    "name": player["name"],
                    "off_ice_count": int(row["off_ice_count"]),
                    "cafe_count": int(row["cafe_count"]),
                    "official_count": int(row["official_count"]),
                    "selected_count": selected_count,
                    "not_selected_count": max(0, total_events - selected_count),
                }
            )
        return stats

    def update_player(self, player_id: int, *, name: str | None = None, ovr: bool | None = None, cafe: bool | None = None) -> None:
        player = self._require_player(player_id)
        new_name = player["name"] if name is None else name.strip() or player["name"]
        new_ovr = bool(player["ovr"]) if ovr is None else bool(ovr)
        new_cafe = bool(player["cafe"]) if cafe is None else bool(cafe)
        self.connection.execute(
            "UPDATE players SET name = ?, ovr = ?, cafe = ? WHERE id = ?",
            (new_name, int(new_ovr), int(new_cafe), player_id),
        )
        self.connection.commit()

    def delete_player(self, player_id: int) -> None:
        self._require_player(player_id)
        self.connection.execute("DELETE FROM team_players WHERE player_id = ?", (player_id,))
        self.connection.execute("DELETE FROM event_players WHERE player_id = ?", (player_id,))
        self.connection.execute("DELETE FROM assignments WHERE player_id = ?", (player_id,))
        self.connection.execute("DELETE FROM players WHERE id = ?", (player_id,))
        self.connection.commit()

    def create_event(self, team_id: int, name: str) -> int:
        self._require_team(team_id)
        return self._insert_and_return_id(
            "INSERT INTO events (team_id, name) VALUES (?, ?)",
            (team_id, name),
        )

    def select_players_for_event(
        self,
        event_id: int,
        player_ids: Iterable[int],
        official_ids: Iterable[int] = (),
    ) -> None:
        event = self._require_event(event_id)
        selected_ids = list(dict.fromkeys(player_ids))
        official_set = {int(player_id) for player_id in official_ids}
        self.connection.execute("DELETE FROM event_players WHERE event_id = ?", (event_id,))
        self.connection.execute("DELETE FROM assignments WHERE event_id = ?", (event_id,))
        for player_id in selected_ids:
            player = self._require_player(player_id)
            if not self._player_belongs_to_team(player_id, event["team_id"]):
                raise ValueError("Selected players must belong to the event team.")
            self.connection.execute(
                "INSERT INTO event_players (event_id, player_id, official) VALUES (?, ?, ?)",
                (event_id, player_id, int(player_id in official_set)),
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
        selected_by_id = {player["player_id"]: player for player in self._selected_players(event_id)}
        for role in manual_assignments:
            for player_id in manual_assignments[role]:
                player_row = selected_by_id.get(player_id)
                if player_row is None:
                    continue
                if bool(player_row["official"]):
                    raise ValueError("Official players are not available for off-ice or cafe duties.")
                if role == ROLE_OFF_ICE and bool(player_row["cafe"]):
                    raise ValueError("Café players are not available for off-ice duties.")
        self.connection.execute(
            "DELETE FROM assignments WHERE event_id = ? AND is_manual = 1",
            (event_id,),
        )
        self._store_manual_assignments(event_id, manual_assignments)
        self.connection.commit()
        return self.assign_fairly(event_id)

    def preview_assignments(
        self,
        event_id: int,
        *,
        selected_player_ids: Iterable[int] | None = None,
        official_ids: Iterable[int] = (),
        exclude_player_ids: Iterable[int] = (),
        randomize: bool = False,
    ) -> dict[str, list[Assignment]]:
        self._require_event(event_id)
        event = self._require_event(event_id)
        player_ids = list(dict.fromkeys(int(player_id) for player_id in (selected_player_ids or ())))
        if player_ids:
            official_set = {int(player_id) for player_id in official_ids}
            selected_players: list[sqlite3.Row] = []
            for player in self.team_players(event["team_id"]):
                if int(player["player_id"]) in player_ids:
                    selected_players.append(
                        {
                            "player_id": int(player["player_id"]),
                            "name": player["name"],
                            "ovr": bool(player["ovr"]),
                            "cafe": bool(player["cafe"]),
                            "official": int(player["player_id"] in official_set),
                            "team_id": event["team_id"],
                        }
                    )
        else:
            selected_players = self._selected_players(event_id)

        excluded_player_ids = {int(player_id) for player_id in exclude_player_ids}
        selected_players = [
            player
            for player in selected_players
            if player["player_id"] not in excluded_player_ids
        ]
        if not selected_players:
            return {ROLE_OFF_ICE: [], ROLE_CAFE: []}

        manual_assignments = self._manual_assignments(event_id)
        return self._build_assignment_plan(
            event_id,
            selected_players,
            manual_assignments,
            randomize=randomize,
            persist=False,
        )

    def confirm_assignments(self, event_id: int, assignments: dict[str, list[int] | list[Assignment]] | None = None) -> dict[str, list[Assignment]]:
        self._require_event(event_id)
        if assignments is None:
            assignments = self.preview_assignments(event_id)

        normalized: dict[str, list[int]] = {ROLE_OFF_ICE: [], ROLE_CAFE: []}
        for role in (ROLE_OFF_ICE, ROLE_CAFE):
            values = assignments.get(role, [])
            normalized[role] = [
                int(item.player_id if isinstance(item, Assignment) else item)
                for item in values
            ]

        self.connection.execute("DELETE FROM assignments WHERE event_id = ?", (event_id,))
        self._store_manual_assignments(event_id, normalized)
        self.connection.commit()
        return self.assign_fairly(event_id)

    def assign_fairly(self, event_id: int, *, exclude_player_ids: Iterable[int] = (), randomize: bool = False) -> dict[str, list[Assignment]]:
        self._require_event(event_id)
        selected_players = self._selected_players(event_id)
        excluded_player_ids = {int(player_id) for player_id in exclude_player_ids}
        selected_players = [
            player
            for player in selected_players
            if player["player_id"] not in excluded_player_ids
        ]
        if not selected_players:
            raise ValueError("Select players for the event before assigning duties.")

        manual_assignments = self._manual_assignments(event_id)
        self._validate_assignments(event_id, manual_assignments)

        assignments = self._build_assignment_plan(
            event_id,
            selected_players,
            manual_assignments,
            randomize=randomize,
            persist=True,
        )
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
                    is_manual=bool(row["is_manual"]),
                )
            )
        return grouped

    def _store_manual_assignments(self, event_id: int, assignments: dict[str, list[int]]) -> None:
        selected_by_id = {player["player_id"]: player for player in self._selected_players(event_id)}
        used_player_ids: set[int] = set()
        for role, player_ids in assignments.items():
            if len(player_ids) > ROLE_LIMITS[role]:
                raise ValueError(f"Too many manual {role} assignments.")
            for player_id in player_ids:
                player = selected_by_id.get(player_id)
                if player is None:
                    raise ValueError("Manual overrides must reference selected event players.")
                if role == ROLE_OFF_ICE and bool(player["cafe"]):
                    raise ValueError("Café players are not available for off-ice duties.")
                if player_id in used_player_ids:
                    raise ValueError("A player can only be assigned to one duty per event.")
                used_player_ids.add(player_id)
                self.connection.execute(
                    "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 1)",
                    (event_id, player_id, role),
                )

    def _build_assignment_plan(
        self,
        event_id: int,
        selected_players: list[sqlite3.Row],
        manual_assignments: list[sqlite3.Row],
        *,
        randomize: bool = False,
        persist: bool = True,
    ) -> dict[str, list[Assignment]]:
        used_player_ids = {assignment["player_id"] for assignment in manual_assignments}
        remaining_players = [
            player
            for player in selected_players
            if not bool(player["official"]) and player["player_id"] not in used_player_ids
        ]

        auto_assignments = {ROLE_OFF_ICE: 0, ROLE_CAFE: 0}
        cafe_players = [player for player in remaining_players if bool(player["cafe"])]
        for _ in range(min(len(cafe_players), ROLE_LIMITS[ROLE_CAFE])):
            candidate = self._choose_candidate(event_id, ROLE_CAFE, cafe_players, randomize=randomize)
            if candidate is None:
                break
            if persist:
                self.connection.execute(
                    "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 0)",
                    (event_id, candidate["player_id"], ROLE_CAFE),
                )
            used_player_ids.add(candidate["player_id"])
            auto_assignments[ROLE_CAFE] += 1
            cafe_players = [
                player
                for player in cafe_players
                if player["player_id"] != candidate["player_id"]
            ]

        remaining_players = [
            player
            for player in remaining_players
            if player["player_id"] not in used_player_ids
        ]

        ovr_players = [player for player in remaining_players if bool(player["ovr"]) ]
        if ovr_players:
            off_ice_ovr_ids = {assignment["player_id"] for assignment in manual_assignments if assignment["role"] == ROLE_OFF_ICE}
            if not any(player["player_id"] in off_ice_ovr_ids for player in ovr_players):
                candidate = self._choose_candidate(event_id, ROLE_OFF_ICE, ovr_players, randomize=randomize)
                if candidate is None:
                    raise ValueError("At least one OVR player must be assigned as an off-ice official.")
                if persist:
                    self.connection.execute(
                        "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 0)",
                        (event_id, candidate["player_id"], ROLE_OFF_ICE),
                    )
                used_player_ids.add(candidate["player_id"])
                auto_assignments[ROLE_OFF_ICE] += 1
                remaining_players = [
                    player
                    for player in remaining_players
                    if player["player_id"] != candidate["player_id"]
                ]

        total_open_slots = sum(
            ROLE_LIMITS[role] - (sum(1 for assignment in manual_assignments if assignment["role"] == role) + auto_assignments.get(role, 0))
            for role in ROLE_LIMITS
        )
        if len(remaining_players) < total_open_slots:
            priority_roles: list[str] = []
            while len(priority_roles) < len(remaining_players):
                off_ice_assigned = sum(1 for role in priority_roles if role == ROLE_OFF_ICE)
                cafe_assigned = sum(1 for role in priority_roles if role == ROLE_CAFE)
                if off_ice_assigned < 3:
                    priority_roles.append(ROLE_OFF_ICE)
                elif cafe_assigned < 2:
                    priority_roles.append(ROLE_CAFE)
                else:
                    priority_roles.append(ROLE_OFF_ICE)

            for role in priority_roles:
                candidate = self._choose_candidate(event_id, role, remaining_players, randomize=randomize)
                if candidate is None:
                    break
                if persist:
                    self.connection.execute(
                        "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 0)",
                        (event_id, candidate["player_id"], role),
                    )
                remaining_players = [
                    player
                    for player in remaining_players
                    if player["player_id"] != candidate["player_id"]
                ]

            return self._group_assignments_for_preview(event_id, selected_players, manual_assignments, randomize=randomize)

        for role in ROLE_ORDER:
            remaining_slots = ROLE_LIMITS[role] - (
                sum(1 for assignment in manual_assignments if assignment["role"] == role)
                + auto_assignments.get(role, 0)
            )
            for _ in range(remaining_slots):
                candidate = self._choose_candidate(event_id, role, remaining_players, randomize=randomize)
                if candidate is None:
                    raise ValueError("Not enough players selected to cover cafe and off ice roles.")
                if persist:
                    self.connection.execute(
                        "INSERT INTO assignments (event_id, player_id, role, is_manual) VALUES (?, ?, ?, 0)",
                        (event_id, candidate["player_id"], role),
                    )
                remaining_players = [
                    player
                    for player in remaining_players
                    if player["player_id"] != candidate["player_id"]
                ]

        if persist:
            return self.get_event_assignments(event_id)
        return self._group_assignments_for_preview(event_id, selected_players, manual_assignments, randomize=randomize)

    def _group_assignments_for_preview(
        self,
        event_id: int,
        selected_players: list[sqlite3.Row],
        manual_assignments: list[sqlite3.Row],
        *,
        randomize: bool = False,
    ) -> dict[str, list[Assignment]]:
        grouped = {ROLE_OFF_ICE: [], ROLE_CAFE: []}
        for assignment in manual_assignments:
            grouped.setdefault(assignment["role"], []).append(
                Assignment(
                    role=assignment["role"],
                    player_id=int(assignment["player_id"]),
                    player_name=self._require_player(assignment["player_id"])["name"],
                    is_manual=True,
                )
            )

        used_player_ids = {assignment["player_id"] for assignment in manual_assignments}
        remaining_players = [
            player
            for player in selected_players
            if not bool(player["official"]) and player["player_id"] not in used_player_ids
        ]

        for role in ROLE_ORDER:
            count = ROLE_LIMITS[role] - sum(1 for assignment in manual_assignments if assignment["role"] == role)
            for _ in range(count):
                candidate = self._choose_candidate(event_id, role, remaining_players, randomize=randomize)
                if candidate is None:
                    break
                grouped[role].append(
                    Assignment(
                        role=role,
                        player_id=int(candidate["player_id"]),
                        player_name=candidate["name"],
                        is_manual=False,
                    )
                )
                used_player_ids.add(candidate["player_id"])
                remaining_players = [
                    player
                    for player in remaining_players
                    if player["player_id"] != candidate["player_id"]
                ]

        return grouped

    def _validate_assignments(self, event_id: int, assignments: list[sqlite3.Row]) -> None:
        selected_by_id = {player["player_id"]: player for player in self._selected_players(event_id)}
        used_player_ids: set[int] = set()
        for role in ROLE_LIMITS:
            role_assignments = [assignment for assignment in assignments if assignment["role"] == role]
            if len(role_assignments) > ROLE_LIMITS[role]:
                raise ValueError(f"Too many {role} assignments for the event.")
        for assignment in assignments:
            player = selected_by_id.get(assignment["player_id"])
            if player is None:
                raise ValueError("Assignments must reference selected event players.")
            if assignment["role"] == ROLE_OFF_ICE and bool(player["cafe"]):
                raise ValueError("Café players are not available for off-ice duties.")
            if assignment["player_id"] in used_player_ids:
                raise ValueError("A player can only be assigned to one duty per event.")
            used_player_ids.add(assignment["player_id"])

    def _choose_candidate(
        self,
        event_id: int,
        role: str,
        players: list[sqlite3.Row],
        *,
        randomize: bool = False,
    ) -> sqlite3.Row | None:
        if not players:
            return None
        if randomize:
            ranked_players = sorted(players, key=lambda player: self._fairness_score(event_id, role, player))
            top_candidates = ranked_players[: max(1, min(3, len(ranked_players)))]
            return random.choice(top_candidates)
        ranked_players = sorted(players, key=lambda player: self._fairness_score(event_id, role, player))
        return ranked_players[0]

    def _fairness_score(self, event_id: int, role: str, player: sqlite3.Row) -> tuple[int, int, int, int, int, int]:
        row = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN assignments.role = ? THEN 1 ELSE 0 END), 0) AS role_count,
                COUNT(*) AS total_count,
                COALESCE(MAX(assignments.event_id), 0) AS last_event_id
            FROM assignments
            WHERE assignments.event_id != ? AND assignments.player_id = ?
            """,
            (role, event_id, player["player_id"]),
        ).fetchone()
        role_history = self.connection.execute(
            """
            SELECT
                COALESCE(SUM(CASE WHEN role = 'off_ice' THEN 1 ELSE 0 END), 0) AS off_ice_count,
                COALESCE(SUM(CASE WHEN role = 'cafe' THEN 1 ELSE 0 END), 0) AS cafe_count
            FROM assignments
            WHERE assignments.player_id = ? AND assignments.event_id != ?
            """,
            (player["player_id"], event_id),
        ).fetchone()
        selected_without_duty_count = self.connection.execute(
            """
            SELECT COUNT(*) AS selected_without_duty_count
            FROM event_players
            LEFT JOIN assignments
                ON assignments.event_id = event_players.event_id
               AND assignments.player_id = event_players.player_id
            JOIN events ON events.id = event_players.event_id
            WHERE event_players.player_id = ?
              AND event_players.event_id != ?
              AND EXISTS (
                  SELECT 1 FROM assignments WHERE assignments.event_id = event_players.event_id
              )
              AND assignments.player_id IS NULL
              AND events.team_id = ?
            """,
            (player["player_id"], event_id, self._require_event(event_id)["team_id"]),
        ).fetchone()["selected_without_duty_count"]
        official_count = self.connection.execute(
            """
            SELECT COUNT(*) AS official_count
            FROM event_players
            JOIN events ON events.id = event_players.event_id
            WHERE event_players.player_id = ? AND event_players.official = 1 AND event_players.event_id != ?
              AND EXISTS (
                  SELECT 1 FROM assignments WHERE assignments.event_id = event_players.event_id
              )
              AND events.team_id = ?
            """,
            (player["player_id"], event_id, self._require_event(event_id)["team_id"]),
        ).fetchone()["official_count"]

        off_ice_count = int(role_history["off_ice_count"])
        cafe_count = int(role_history["cafe_count"])
        role_bias = 0
        if role == ROLE_CAFE:
            role_bias = max(0, cafe_count - 1) * 10
        elif role == ROLE_OFF_ICE:
            role_bias = max(0, off_ice_count - 2) * 10

        return (
            row["role_count"] + official_count * 2 + int(role_bias),
            row["total_count"] + selected_without_duty_count + official_count * 2,
            row["last_event_id"],
            selected_without_duty_count,
            int(role_bias),
            player["player_id"],
        )

    def _manual_assignments(self, event_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT assignments.role, assignments.player_id
            FROM assignments
            WHERE assignments.event_id = ? AND assignments.is_manual = 1
            ORDER BY assignments.role, assignments.player_id
            """,
            (event_id,),
        ).fetchall()

    def _selected_players(self, event_id: int) -> list[sqlite3.Row]:
        return self.connection.execute(
            """
            SELECT players.id AS player_id, team_players.team_id, players.name, players.ovr, players.cafe, event_players.official
            FROM event_players
            JOIN players ON players.id = event_players.player_id
            LEFT JOIN team_players ON team_players.player_id = players.id
            WHERE event_players.event_id = ?
            ORDER BY players.id
            """,
            (event_id,),
        ).fetchall()

    def _player_belongs_to_team(self, player_id: int, team_id: int) -> bool:
        row = self.connection.execute(
            "SELECT 1 FROM team_players WHERE team_id = ? AND player_id = ?",
            (team_id, player_id),
        ).fetchone()
        return row is not None

    def _require_team(self, team_id: int) -> sqlite3.Row:
        team = self.connection.execute("SELECT id, name FROM teams WHERE id = ?", (team_id,)).fetchone()
        if team is None:
            raise ValueError("Unknown team.")
        return team

    def _require_player(self, player_id: int) -> sqlite3.Row:
        player = self.connection.execute(
            "SELECT id, name, ovr, cafe FROM players WHERE id = ?",
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
                name TEXT NOT NULL,
                ovr INTEGER NOT NULL DEFAULT 0 CHECK (ovr IN (0, 1)),
                cafe INTEGER NOT NULL DEFAULT 0 CHECK (cafe IN (0, 1))
            );

            CREATE TABLE IF NOT EXISTS team_players (
                team_id INTEGER NOT NULL,
                player_id INTEGER NOT NULL,
                PRIMARY KEY (team_id, player_id),
                FOREIGN KEY (team_id) REFERENCES teams (id),
                FOREIGN KEY (player_id) REFERENCES players (id)
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
                official INTEGER NOT NULL DEFAULT 0 CHECK (official IN (0, 1)),
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

        player_columns = [
            row[1]
            for row in self.connection.execute("PRAGMA table_info(players)").fetchall()
        ]

        if player_columns and ("team_id" in player_columns or "parent_name" in player_columns or "ovr" not in player_columns or "cafe" not in player_columns):
            self.connection.execute("ALTER TABLE players RENAME TO players_legacy")
            self.connection.execute(
                """
                CREATE TABLE players (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    name TEXT NOT NULL,
                    ovr INTEGER NOT NULL DEFAULT 0 CHECK (ovr IN (0, 1)),
                    cafe INTEGER NOT NULL DEFAULT 0 CHECK (cafe IN (0, 1))
                )
                """
            )
            legacy_columns = [
                row[1]
                for row in self.connection.execute("PRAGMA table_info(players_legacy)").fetchall()
            ]
            ovr_expression = "COALESCE(ovr, 0)" if "ovr" in legacy_columns else "0"
            self.connection.execute(
                f"""
                INSERT INTO players (id, name, ovr, cafe)
                SELECT id, name, {ovr_expression}, 0
                FROM players_legacy
                """
            )
            if "team_id" in legacy_columns:
                self.connection.execute(
                    """
                    INSERT INTO team_players (team_id, player_id)
                    SELECT team_id, id
                    FROM players_legacy
                    WHERE team_id IS NOT NULL
                    """
                )
            self.connection.execute("DROP TABLE players_legacy")

        self.connection.execute(
            "CREATE TABLE IF NOT EXISTS team_players (team_id INTEGER NOT NULL, player_id INTEGER NOT NULL, PRIMARY KEY (team_id, player_id), FOREIGN KEY (team_id) REFERENCES teams (id), FOREIGN KEY (player_id) REFERENCES players (id))"
        )

        event_player_columns = [
            row[1]
            for row in self.connection.execute("PRAGMA table_info(event_players)").fetchall()
        ]
        if "official" not in event_player_columns:
            self.connection.execute("ALTER TABLE event_players ADD COLUMN official INTEGER NOT NULL DEFAULT 0")

        self.connection.commit()
