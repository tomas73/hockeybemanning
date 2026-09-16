# hockeybemanning

Small Python app for managing hockey teams, event selections, and fair parent duty assignment.

## What it supports

- create teams
- assign players (with parent names) to teams
- create events for a team
- select the players picked for an event
- auto-assign 5 off ice officials and 2 cafe workers fairly over time
- manually override some or all assignments and let the app fill the rest fairly

## Example

```python
from hockeybemanning import HockeySchedulingApp

app = HockeySchedulingApp("club.db")
team_id = app.create_team("U12")
player_id = app.add_player(team_id, "Alice", "Sam Andersson")
event_id = app.create_event(team_id, "Home game vs Tigers")
app.select_players_for_event(event_id, [player_id])
```

The scheduling logic tracks fairness by parent across previous events in the same database.
Deployment choices are intentionally left for a later discussion.
