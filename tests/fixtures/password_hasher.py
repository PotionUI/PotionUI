from src.platform.security import PasswordHasher


class CheapPasswordHasher(PasswordHasher):
    ROUNDS = 4
