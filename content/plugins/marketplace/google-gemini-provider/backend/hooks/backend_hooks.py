from src.plugin_api.cloud import register_cloud_provider


def register_backend(context):
    from ..provider import GoogleProvider

    register_cloud_provider(context, GoogleProvider)
    return context
