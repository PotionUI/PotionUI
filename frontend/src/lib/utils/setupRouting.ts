import type { SetupStatus } from '$lib/services/api/setup';

/**
 * Root-route decision: where `/` sends the client.
 *
 * `needs_owner` wins regardless of auth state - an unclaimed instance always
 * routes to the claim screen, even if (implausibly) a session is present. A
 * null `status` means the status fetch failed; fail soft into the pre-setup
 * behavior (auth-only routing) rather than bricking the app on a new endpoint.
 */
export function decideRootDestination(status: SetupStatus | null, isAuthenticated: boolean): string {
	if (status?.needs_owner) return '/setup/claim';
	return isAuthenticated ? '/generate' : '/login';
}

/**
 * True once `needs_owner` is settled (false) but the fetch is still needed to
 * decide whether that owner-independent decision requires waiting on auth
 * state. Kept separate from `decideRootDestination` so the root page doesn't
 * have to wait on a slow auth check when the answer is already known.
 */
export function canDecideRootDestination(status: SetupStatus | null, authLoading: boolean): boolean {
	if (status?.needs_owner) return true;
	return !authLoading;
}

/**
 * `/register` shows the owner-exists message instead of the form once the
 * instance has an owner and registration is closed. While unclaimed,
 * registration is always open (someone has to become the owner), so this is
 * false whenever `needs_owner` is true regardless of `registration_open`.
 */
export function shouldBlockRegistration(status: SetupStatus | null): boolean {
	if (!status) return false;
	return !status.needs_owner && !status.registration_open;
}

/**
 * `/login`: whether to show the "Register here" link. A link to a page that
 * will just tell the visitor to go away is worse than no link, so this
 * mirrors `shouldBlockRegistration` from the opposite direction - same
 * fail-soft default (an unresolved or failed status check shows the link;
 * submitting still hits the real server-side gate).
 */
export function shouldShowRegisterLink(status: SetupStatus | null): boolean {
	return !shouldBlockRegistration(status);
}
