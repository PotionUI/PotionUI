import mimetypes

WEB_ASSET_MIME_TYPES: dict[str, str] = {
    ".js": "text/javascript",
    ".mjs": "text/javascript",
    ".css": "text/css",
    ".wasm": "application/wasm",
    ".json": "application/json",
    ".map": "application/json",
    ".svg": "image/svg+xml",
    ".html": "text/html",
    ".webmanifest": "application/manifest+json",
    ".woff": "font/woff",
    ".woff2": "font/woff2",
    ".ttf": "font/ttf",
}


def ensure_web_mime_types() -> None:
    mimetypes.init()
    for extension, mime_type in WEB_ASSET_MIME_TYPES.items():
        mimetypes.add_type(mime_type, extension, strict=True)
