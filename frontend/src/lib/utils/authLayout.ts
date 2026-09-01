/**
 * Root-layout decision: what the shell renders while auth state settles.
 *
 * The login/register/claim routes are only ever the right thing to show once
 * we've definitively established the visitor is logged out. A signed-in user
 * who is still parked on one of those routes - either because the boot auth
 * check hasn't redirected them away yet, or because a just-completed login
 * hasn't finished the client-side navigation to /generate - must see a
 * neutral state instead. Without this, the (SPA-only, no page reload
 * involved) window between "isAuthenticated flips true" and "goto('/generate')
 * finishes loading the destination route" re-renders the public branch, and
 * with it the login form, for as long as that navigation takes.
 */
export type AuthLayoutMode = 'boot' | 'app' | 'redirecting' | 'public';

export interface AuthLayoutState {
	mounted: boolean;
	loading: boolean;
	isAuthenticated: boolean;
	isPublicRoute: boolean;
}

export function resolveAuthLayoutMode(state: AuthLayoutState): AuthLayoutMode {
	if (state.loading) return 'boot';
	if (state.isAuthenticated && state.isPublicRoute) return 'redirecting';
	if (state.mounted && state.isAuthenticated && !state.isPublicRoute) return 'app';
	return 'public';
}
