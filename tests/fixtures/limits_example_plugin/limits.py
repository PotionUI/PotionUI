from src.plugin_api.limits import Usage

PROJECTS = {}


def projects(context):
    return Usage(used=PROJECTS.get(context.user_id, 0))


def only_big_uploads(request):
    return (request.incoming_bytes or 0) >= 100
