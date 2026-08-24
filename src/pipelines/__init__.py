"""Pipes: the contract, the vocabulary they speak, and the catalog of them.

- contracts.py: what a pipe is - BasePipe, the IOType vocabulary, the input/output/config specs.
- outputs.py:   what a pipe emits while it runs - progress, previews, artifacts, the gallery.
- models.py:    the model objects that travel between pipes, and the families they belong to.
- catalog.py:   which pipes exist - discovery from the app, from `pipes/custom`, and from plugins.
- installer.py: whether a pipe's requirements are met, and how to meet them.
- graph.py:     a processed pipe list projected into nodes and connections, for the preview API.
- pipes/:       the pipes themselves.

This package sits below the features that drive it and imports none of them.
Assembling a pipeline from a preset is orchestration, and lives with generation
(`src.features.generation.pipeline_builder`); so does deciding what to do with an
emitted output. Nothing is re-exported here - import from the modules directly.
"""
