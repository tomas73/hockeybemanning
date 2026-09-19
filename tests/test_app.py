import sqlite3
import tempfile
import unittest

from hockeybemanning import HockeySchedulingApp


class HockeySchedulingAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = HockeySchedulingApp()
        self.team_id = self.app.create_team("U12")
        self.player_ids = [
            self.app.add_player(self.team_id, f"Player {index}")
            for index in range(1, 9)
        ]

    def tearDown(self) -> None:
        self.app.close()

    def test_assigns_five_off_ice_and_two_cafe_from_selected_players(self) -> None:
        event_id = self.app.create_event(self.team_id, "Match 1")
        self.app.select_players_for_event(event_id, self.player_ids[:7])

        assignments = self.app.assign_fairly(event_id)

        self.assertEqual(5, len(assignments["off_ice"]))
        self.assertEqual(2, len(assignments["cafe"]))
        assigned_player_ids = {
            assignment.player_id
            for role_assignments in assignments.values()
            for assignment in role_assignments
        }
        self.assertEqual(set(self.player_ids[:7]), assigned_player_ids)
        self.assertEqual(7, len(assigned_player_ids))

    def test_rotates_cafe_assignments_before_repeating_the_same_parents(self) -> None:
        first_event = self.app.create_event(self.team_id, "Match 1")
        second_event = self.app.create_event(self.team_id, "Match 2")
        self.app.select_players_for_event(first_event, self.player_ids)
        self.app.select_players_for_event(second_event, self.player_ids)

        first_assignments = self.app.assign_fairly(first_event)
        second_assignments = self.app.assign_fairly(second_event)

        first_cafe_players = {assignment.player_id for assignment in first_assignments["cafe"]}
        second_cafe_players = {assignment.player_id for assignment in second_assignments["cafe"]}
        self.assertTrue(first_cafe_players.isdisjoint(second_cafe_players))

    def test_manual_overrides_are_preserved_and_remaining_slots_are_filled(self) -> None:
        event_id = self.app.create_event(self.team_id, "Match 1")
        self.app.select_players_for_event(event_id, self.player_ids[:7])

        assignments = self.app.override_assignments(
            event_id,
            off_ice_player_ids=[self.player_ids[0]],
            cafe_player_ids=[self.player_ids[6]],
        )

        self.assertEqual(5, len(assignments["off_ice"]))
        self.assertEqual(2, len(assignments["cafe"]))
        self.assertEqual({self.player_ids[0]}, {assignment.player_id for assignment in assignments["off_ice"] if assignment.is_manual})
        self.assertEqual({self.player_ids[6]}, {assignment.player_id for assignment in assignments["cafe"] if assignment.is_manual})

    def test_assigns_at_least_one_ovr_player_to_off_ice(self) -> None:
        ovr_player_id = self.app.add_player(self.team_id, "OVR Player", ovr=True)
        selected_ids = [ovr_player_id] + self.player_ids[:6]
        event_id = self.app.create_event(self.team_id, "Match 1")
        self.app.select_players_for_event(event_id, selected_ids)

        assignments = self.app.assign_fairly(event_id)

        self.assertTrue(any(assignment.player_id == ovr_player_id for assignment in assignments["off_ice"]))
        self.assertEqual(5, len(assignments["off_ice"]))

    def test_ovr_player_does_not_reduce_off_ice_count_when_full_quota_is_available(self) -> None:
        ovr_player_id = self.app.add_player(self.team_id, "OVR Player", ovr=True)
        selected_ids = self.player_ids[:6] + [ovr_player_id]
        event_id = self.app.create_event(self.team_id, "Match 1")
        self.app.select_players_for_event(event_id, selected_ids)

        assignments = self.app.assign_fairly(event_id)

        self.assertEqual(5, len(assignments["off_ice"]))
        self.assertEqual(2, len(assignments["cafe"]))
        self.assertIn(ovr_player_id, {assignment.player_id for assignment in assignments["off_ice"]})

    def test_short_roster_with_three_players_uses_off_ice_only(self) -> None:
        sibling_team_id = self.app.create_team("U10")
        sibling_player_ids = [
            self.app.add_player(sibling_team_id, f"Sibling {index}")
            for index in range(1, 4)
        ]
        event_id = self.app.create_event(sibling_team_id, "Match")
        self.app.select_players_for_event(event_id, sibling_player_ids)

        assignments = self.app.assign_fairly(event_id)

        self.assertEqual(3, len(assignments["off_ice"]))
        self.assertEqual(0, len(assignments["cafe"]))

    def test_short_roster_prioritizes_three_off_ice_then_two_cafe_then_off_ice(self) -> None:
        event_id = self.app.create_event(self.team_id, "Short Match")
        selected_ids = self.player_ids[:5]
        self.app.select_players_for_event(event_id, selected_ids)

        assignments = self.app.assign_fairly(event_id)

        self.assertEqual(3, len(assignments["off_ice"]))
        self.assertEqual(2, len(assignments["cafe"]))

    def test_cafe_players_are_assigned_to_cafe_and_not_off_ice(self) -> None:
        cafe_player_id = self.app.add_player(self.team_id, "Cafe Player", cafe=True)
        event_id = self.app.create_event(self.team_id, "Match 1")
        self.app.select_players_for_event(event_id, self.player_ids[:7] + [cafe_player_id])

        assignments = self.app.assign_fairly(event_id)

        self.assertIn(cafe_player_id, {assignment.player_id for assignment in assignments["cafe"]})
        self.assertNotIn(cafe_player_id, {assignment.player_id for assignment in assignments["off_ice"]})

    def test_migrates_legacy_database_schema_without_parent_name(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".db") as db_file:
            conn = sqlite3.connect(db_file.name)
            conn.execute("CREATE TABLE teams (id INTEGER PRIMARY KEY AUTOINCREMENT, name TEXT NOT NULL UNIQUE)")
            conn.execute("CREATE TABLE players (id INTEGER PRIMARY KEY AUTOINCREMENT, team_id INTEGER NOT NULL, name TEXT NOT NULL, parent_name TEXT NOT NULL, FOREIGN KEY (team_id) REFERENCES teams (id))")
            team_id = conn.execute("INSERT INTO teams (name) VALUES (?)", ("Legacy",)).lastrowid
            conn.execute("INSERT INTO players (team_id, name, parent_name) VALUES (?, ?, ?)", (team_id, "Legacy Player", "Guardian"))
            conn.commit()
            conn.close()

            migrated = HockeySchedulingApp(db_file.name)
            team_id = migrated.create_team("New Team")
            migrated.add_player(team_id, "New Player", ovr=True)

            player_columns = [column[1] for column in migrated.connection.execute("PRAGMA table_info(players)").fetchall()]
            self.assertIn("ovr", player_columns)
            self.assertNotIn("parent_name", player_columns)
            self.assertTrue(migrated.connection.execute("SELECT ovr FROM players WHERE name = ?", ("New Player",)).fetchone()[0])
            migrated.close()

    def test_players_are_created_globally_and_then_assigned_to_a_team(self) -> None:
        team_id = self.app.create_team("U14")
        player_id = self.app.add_player("Global Player", ovr=True)

        self.app.assign_player_to_team(team_id, player_id)

        assigned_player_ids = [row["player_id"] for row in self.app.team_players(team_id)]
        self.assertIn(player_id, assigned_player_ids)
        self.assertTrue(self.app.connection.execute("SELECT ovr FROM players WHERE id = ?", (player_id,)).fetchone()[0])

    def test_official_players_count_as_work_but_stay_out_of_duties(self) -> None:
        team_id = self.app.create_team("U12 Official")
        player_ids = [self.app.add_player(f"Player {index}") for index in range(1, 9)]
        for player_id in player_ids:
            self.app.assign_player_to_team(team_id, player_id)

        first_event_id = self.app.create_event(team_id, "Match 1")
        self.app.select_players_for_event(first_event_id, player_ids, official_ids=[player_ids[0]])
        self.app.assign_fairly(first_event_id)

        second_event_id = self.app.create_event(team_id, "Match 2")
        self.app.select_players_for_event(second_event_id, player_ids)
        second_assignments = self.app.assign_fairly(second_event_id)

        assigned_player_ids = {
            assignment.player_id
            for role_assignments in second_assignments.values()
            for assignment in role_assignments
        }
        self.assertNotIn(player_ids[0], assigned_player_ids)

    def test_unconfirmed_events_do_not_count_toward_not_selected_totals(self) -> None:
        team_id = self.app.create_team("U12 Unconfirmed")
        player_ids = [self.app.add_player(team_id, f"Player {idx}") for idx in range(1, 4)]
        for player_id in player_ids:
            self.app.assign_player_to_team(team_id, player_id)

        event_id = self.app.create_event(team_id, "Draft event")
        self.app.select_players_for_event(event_id, player_ids)

        stats = {stat["player_id"]: stat for stat in self.app.team_player_stats(team_id)}
        for player_id in player_ids:
            self.assertEqual(0, stats[player_id]["not_selected_count"])


if __name__ == "__main__":
    unittest.main()
