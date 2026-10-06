from src.plugin_api.cloud import register_cloud_provider


def register_backend(context):
    from ..provider import OpenAIProvider

    register_cloud_provider(context, OpenAIProvider)
    return context
