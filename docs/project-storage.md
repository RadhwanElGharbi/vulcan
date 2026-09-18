# Project storage

In Project Index, choose **Choose directory** to open the operating system's native
folder picker, then select a folder. Cancelling leaves the location unchanged.
For the local browser app, the API opens the picker on the computer running VULCAN;
the desktop app uses its native Electron picker. New projects are saved in a named subfolder
there. Creation shows the destination and rejects a stale destination if another
window changes it before submission.

Project Index discovers existing projects in the selected folder and in previously
added locations. Existing projects stay where they are; selecting a new location
does not copy or relocate them. Choose a workspace folder containing projects,
rather than an individual project folder. Duplicate project names across locations
are rejected so jobs and project URLs cannot resolve to the wrong project.

The existing layout is preserved: `project_metadata.json`, `aoi/`, raw and
processed data folders, logs, and the scientific pipeline's retained inputs,
generation manifests and provenance. Discovery stops at each project boundary.
All readers and new fetch jobs resolve the project through the same directory
registry, including the separate acquisition worker.

The default location and known locations are stored atomically in
`.runtime/workspace.json`. `ZEUS_WORKSPACE_SETTINGS` can override this settings
file path. The original `AGRS_PROJECTS_ROOT` remains indexed. Settings are read by
both API and worker processes and survive restarts. An invalid settings file
causes an error rather than silently changing the save location.

Verification: `tests/test_workspace_directory.py` covers discovery, persistence in a
fresh process, duplicate names, invalid destinations, failed settings writes,
standard project creation and reading the saved project after changing location.
