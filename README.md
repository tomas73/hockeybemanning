# hockeybemanning

Personal web app for managing hockey teams, event selections, and fair role assignment.

This is intended as a localhost-first tool for one person, not a public app. The current draft can be treated as disposable if a cleaner structure is easier.

## Recommended stack

- Backend: Flask
- Templates: Jinja2
- Small interactivity: HTMX
- Database: SQLite
- Styling: plain CSS or a tiny utility framework like Pico.css

This keeps the app simple, avoids a frontend build pipeline, and makes later container deployment on a NAS straightforward.

## Development setup

Use a VS Code devcontainer for development so your local machine stays clean.

- Devcontainer: isolated Python environment for editing, testing, and running the app locally
- Runtime: still native if you want it, or containerized later on a NAS if that becomes useful

The devcontainer should be the only place where Python packages for this app are installed during development.

## Page flow

1. Dashboard
	- shows teams
	- shows upcoming events
	- gives quick access to create a team or event
2. Team page
	- lists players and parents
	- lets you add, edit, or remove players
3. Event page
	- shows selected players for the event
	- shows current assignments
	- has a single action to auto-fill fairly
	- lets you override specific roles manually and refill the rest
4. History view
	- shows past assignments so you can see how fairness has evolved

## Target folder structure

```text
hockeybemanning/
	.devcontainer/
		devcontainer.json
	hockeybemanning/
		__init__.py
		core.py            # fair assignment logic and database access
		web.py             # Flask app and routes
		templates/
			base.html
			dashboard.html
			team.html
			event.html
			history.html
		static/
			styles.css
	requirements.txt
tests/
	test_core.py
	test_web.py
```

## Deployment shape

Start with a local server on your machine. Later, the same app can run in a container on your NAS with the SQLite database stored on a mounted volume so you can open it from any computer on your network.

## Core behavior

- create teams
- assign players to teams
- create events for a team
- select the players picked for an event
- auto-assign 5 off-ice officials and 2 cafe workers fairly over time
- manually override some or all assignments and let the app fill the rest fairly

## Example

```python
from hockeybemanning import HockeySchedulingApp

app = HockeySchedulingApp("club.db")
team_id = app.create_team("U12")
player_id = app.add_player(team_id, "Alice")
event_id = app.create_event(team_id, "Home game vs Tigers")
app.select_players_for_event(event_id, [player_id])
```

The scheduling logic tracks fairness across previous events in the same database.
