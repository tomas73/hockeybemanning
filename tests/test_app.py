import unittest

from hockeybemanning import HockeySchedulingApp


class HockeySchedulingAppTests(unittest.TestCase):
    def setUp(self) -> None:
        self.app = HockeySchedulingApp()
        self.team_id = self.app.create_team("U12")
        self.player_ids = [
            self.app.add_player(self.team_id, f"Player {index}", f"Parent {index}")
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
        assigned_parents = {
            assignment.parent_name
            for role_assignments in assignments.values()
            for assignment in role_assignments
        }
        self.assertEqual(7, len(assigned_parents))

    def test_rotates_cafe_assignments_before_repeating_the_same_parents(self) -> None:
        first_event = self.app.create_event(self.team_id, "Match 1")
        second_event = self.app.create_event(self.team_id, "Match 2")
        self.app.select_players_for_event(first_event, self.player_ids)
        self.app.select_players_for_event(second_event, self.player_ids)

        first_assignments = self.app.assign_fairly(first_event)
        second_assignments = self.app.assign_fairly(second_event)

        first_cafe_parents = {assignment.parent_name for assignment in first_assignments["cafe"]}
        second_cafe_parents = {assignment.parent_name for assignment in second_assignments["cafe"]}
        self.assertTrue(first_cafe_parents.isdisjoint(second_cafe_parents))

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

    def test_requires_enough_distinct_parents_to_cover_all_roles(self) -> None:
        sibling_team_id = self.app.create_team("U10")
        sibling_player_ids = [
            self.app.add_player(sibling_team_id, f"Sibling {index}", f"Family {((index - 1) // 2) + 1}")
            for index in range(1, 8)
        ]
        event_id = self.app.create_event(sibling_team_id, "Match")
        self.app.select_players_for_event(event_id, sibling_player_ids)

        with self.assertRaisesRegex(ValueError, "Not enough distinct parents"):
            self.app.assign_fairly(event_id)


if __name__ == "__main__":
    unittest.main()
