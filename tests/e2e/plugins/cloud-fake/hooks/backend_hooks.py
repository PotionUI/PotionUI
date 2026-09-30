from src.plugin_api import HookContext
from src.plugin_api.cloud import register_cloud_provider


def register_backend(context: HookContext) -> HookContext:
    from ..provider.e2e_fake import E2eFakeProvider

    register_cloud_provider(context, E2eFakeProvider)
    return context
