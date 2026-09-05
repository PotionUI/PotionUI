// A session command (autosave, save, save-as, restore, delete) resolves long
// after the user may have picked another session or switched tab, and its
// completion writes tab-scoped state (`recordSavedBaseline` -> tabsStore). The
// session list entry for the saved id is always safe to refresh; the ACTIVE
// state — currentSession, the saved baseline, the dirty/saving flags, the
// error — must only be written while the command still owns the selection it
// started under.

/** Captured when an async session command starts. */
export interface SessionCommandToken {
	/** The session selected at the start of the command; '' when none was. */
	sessionId: string;
	/** The selection generation the command started under. */
	generation: number;
}

export function sessionCommandOwnsActiveState(
	token: SessionCommandToken,
	currentGeneration: number,
	activeSessionId: string
): boolean {
	return token.generation === currentGeneration && token.sessionId === activeSessionId;
}
