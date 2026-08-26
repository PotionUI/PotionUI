"""
Workspace administration.

`operations` (`operations/`) drives workspace mutations on behalf of
`WorkspaceController` (`routes.py`): module-level functions that create,
update and delete user workspaces (saved tab layout configurations), taking
the repository as an explicit argument. Reads are pure repository calls made
directly by the controller.
"""
