"""
Migration 047: Fix browser-conflicting keybinding defaults
Changes Ctrl+K, Ctrl+T, Ctrl+W, Ctrl+B to non-modifier alternatives
that don't conflict with browser shortcuts.
"""

from src.platform.database.database import db


def up():
    """Update keybinding defaults that conflict with browser shortcuts"""
    with db.get_cursor() as cursor:
        # Ctrl+K (browser address bar) -> / (no modifier)
        cursor.execute(
            "UPDATE keybinding_defaults SET key = '/', modifiers = '' WHERE id = 'quick_search' AND key = 'k' AND modifiers = 'ctrl'"
        )

        # Ctrl+T (browser new tab) -> t (no modifier, context-specific to generate)
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 't', modifiers = '' WHERE id = 'new_tab' AND key = 't' AND modifiers = 'ctrl'"
        )

        # Ctrl+W (browser close tab) -> x (no modifier, context-specific to generate)
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 'x', modifiers = '' WHERE id = 'close_tab' AND key = 'w' AND modifiers = 'ctrl'"
        )

        # Ctrl+B (browser bookmarks) -> b (no modifier)
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 'b', modifiers = '' WHERE id = 'toggle_sidebar' AND key = 'b' AND modifiers = 'ctrl'"
        )

        # Also update any user overrides that still reference the old defaults
        # (only if user hasn't customized - i.e. if their override matches the old default)
        cursor.execute(
            "UPDATE user_keybindings SET key = '/', modifiers = '' WHERE action_id = 'quick_search' AND key = 'k' AND modifiers = 'ctrl'"
        )
        cursor.execute(
            "UPDATE user_keybindings SET key = 't', modifiers = '' WHERE action_id = 'new_tab' AND key = 't' AND modifiers = 'ctrl'"
        )
        cursor.execute(
            "UPDATE user_keybindings SET key = 'x', modifiers = '' WHERE action_id = 'close_tab' AND key = 'w' AND modifiers = 'ctrl'"
        )
        cursor.execute(
            "UPDATE user_keybindings SET key = 'b', modifiers = '' WHERE action_id = 'toggle_sidebar' AND key = 'b' AND modifiers = 'ctrl'"
        )

        print("Migration 047: Fixed browser-conflicting keybinding defaults")


def down():
    """Revert to original Ctrl+key defaults"""
    with db.get_cursor() as cursor:
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 'k', modifiers = 'ctrl' WHERE id = 'quick_search' AND key = '/' AND modifiers = ''"
        )
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 't', modifiers = 'ctrl' WHERE id = 'new_tab' AND key = 't' AND modifiers = ''"
        )
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 'w', modifiers = 'ctrl' WHERE id = 'close_tab' AND key = 'x' AND modifiers = ''"
        )
        cursor.execute(
            "UPDATE keybinding_defaults SET key = 'b', modifiers = 'ctrl' WHERE id = 'toggle_sidebar' AND key = 'b' AND modifiers = ''"
        )

        print("Migration 047: Reverted browser-conflicting keybinding fix")
