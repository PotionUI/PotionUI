"""
Model library administration.

`operations` (`operations/`) drives model collection mutations on behalf of
`ModelCollectionController` (`routes.py`): module-level functions taking a
`ModelCollectionRepository` as an explicit argument. `list_collections` is a
pure repository call made directly by the controller; `get_collection` is the
shared ownership-checked lookup every mutation needs. The per-user
favorite/custom-name overlay on models
(`UserModelMetaRepository.set_favorite`/`set_custom_name`) has no validation
logic of its own and is called directly by `ModelController`
(`src.features.models.routes`).
"""
