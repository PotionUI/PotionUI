/**
 * Pure logic for the route/session scope-mismatch notice docked above the
 * composer (see ChatScopeBanner.svelte). A session's mode is fixed once the
 * session exists server-side; navigating to a route whose resolved mode
 * differs from the active session's mode doesn't switch the session — it
 * surfaces this mismatch instead.
 */

export interface ScopeDismissal {
	sessionId: string | null;
	routeMode: string;
}

/** True when the active session's mode differs from the route's resolved mode. */
export function isScopeMismatched(sessionMode: string, routeMode: string): boolean {
	return sessionMode !== routeMode;
}

/**
 * Whether the mismatch notice should render. Dismissing it only silences the
 * current (sessionId, routeMode) pairing — switching sessions or navigating
 * to a route with a different resolved mode reinstates it.
 */
export function shouldShowScopeMismatch(
	sessionMode: string,
	routeMode: string,
	sessionId: string | null,
	dismissed: ScopeDismissal | null
): boolean {
	if (!isScopeMismatched(sessionMode, routeMode)) return false;
	if (!dismissed) return true;
	return dismissed.sessionId !== sessionId || dismissed.routeMode !== routeMode;
}
