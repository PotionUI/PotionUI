"""
Session administration.

`operations` (`operations/`) drives session mutations on behalf of
`SessionController` (`routes.py`): module-level functions that create, update
and delete user sessions (saved preset configurations), taking the
repository/plugin-registry/version-repository collaborators as explicit
arguments. Reads are pure repository calls made directly by the controller.
"""
