from src.features.api_keys.operations import keys as operations


class World:
    def __init__(self, stack):
        self.stack = stack
        self.sessions = {}
        self.handle_method = None
        self.alice = stack.user("alice")
        self.bob = stack.user("bob")
        self.root = stack.user("root", admin=True)
        for name, user in (("alice", self.alice), ("bob", self.bob), ("root", self.root)):
            self.sessions[f"jwt.{name}.token"] = user

    def session(self, name):
        return {"Authorization": f"Bearer jwt.{name}.token"}

    def mint(self, owner, scopes=("read",), days=None, name="script"):
        key, secret = operations.mint(self.stack.collaborators, owner, name, list(scopes), days, True)
        return key, secret

    @staticmethod
    def bearer(secret):
        return {"Authorization": f"Bearer {secret}"}
