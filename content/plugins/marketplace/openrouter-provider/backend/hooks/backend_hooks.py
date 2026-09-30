from src.plugin_api.cloud import register_cloud_provider


def register_backend(context):
    from ..provider import OpenRouterProvider

    register_cloud_provider(context, OpenRouterProvider)
    return context
