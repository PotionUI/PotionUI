import type { SetupStatus } from '$lib/services/api/setup';

export function decideRootDestination(status: SetupStatus | null, isAuthenticated: boolean): string | null {
	if (!status) return null;
	if (status.needs_owner) return '/setup/claim';
	return isAuthenticated ? '/generate' : '/login';
}

export function canDecideRootDestination(status: SetupStatus | null, authLoading: boolean): boolean {
	if (!status) return false;
	if (status.needs_owner) return true;
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
