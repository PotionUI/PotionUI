"""Asset-fetching port: the layering-legal seam between pipes and downloads.

`src/pipelines/` may not import `src.features` (enforced by
`tests/architecture/test_layering.py`), and the one download manager lives in
`src.features.downloads`. This package holds the *contract* a pipe programs
against so the concrete manager can be injected across that boundary. It
deliberately contains no fetching machinery of its own - a second
implementation here would recreate the invisible parallel download path that
routing everything through one manager exists to prevent.
"""

from src.platform.assets.fetcher import AssetFetchError, AssetFetcher, asset_subdir

__all__ = ["AssetFetchError", "AssetFetcher", "asset_subdir"]
