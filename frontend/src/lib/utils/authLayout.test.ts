import { describe, it, expect } from 'vitest';
import { resolveAuthLayoutMode, shouldRedirectToLogin, type AuthLayoutState } from './authLayout';

function state(overrides: Partial<AuthLayoutState> = {}): AuthLayoutState {
	return {
		mounted: true,
		loading: false,
		isAuthenticated: false,
		isPublicRoute: false,
		...overrides
	};
}

describe('resolveAuthLayoutMode', () => {
	it('shows boot while the auth check is in flight, regardless of route', () => {
		expect(resolveAuthLayoutMode(state({ loading: true, isPublicRoute: true }))).toBe('boot');
		expect(resolveAuthLayoutMode(state({ loading: true, isPublicRoute: false }))).toBe('boot');
	});

	it('shows the app shell once mounted, authenticated, and off a public route', () => {
		expect(resolveAuthLayoutMode(state({ isAuthenticated: true, isPublicRoute: false }))).toBe(
			'app'
		);
	});

	it('holds the neutral boot state before mount for an authenticated user, so the page is never rendered in the public branch and then again in the app branch', () => {
		expect(
			resolveAuthLayoutMode(state({ mounted: false, isAuthenticated: true, isPublicRoute: false }))
		).toBe('boot');
	});

	it('shows a neutral redirecting state for an authenticated user still on a public route', () => {
		// This is the login-flash bug: login just succeeded (isAuthenticated
		// true, loading false) but goto('/generate') hasn't finished yet, so
		// the route is still /login. The login form must not reappear here.
		expect(resolveAuthLayoutMode(state({ isAuthenticated: true, isPublicRoute: true }))).toBe(
			'redirecting'
		);
	});

	it('shows the public route content only once logged-out is definitive', () => {
		expect(resolveAuthLayoutMode(state({ isAuthenticated: false, isPublicRoute: true }))).toBe(
			'public'
		);
	});

	it('shows public content for a logged-out visitor on a protected route (redirect guard handles the rest)', () => {
		expect(resolveAuthLayoutMode(state({ isAuthenticated: false, isPublicRoute: false }))).toBe(
			'public'
		);
	});
});

describe('shouldRedirectToLogin', () => {
	const signedOut = { mounted: true, loading: false, isAuthenticated: false, isPublicRoute: false };

	it('sends a signed-out visitor on a protected route to login', () => {
		expect(shouldRedirectToLogin({ ...signedOut, pathname: '/generate' })).toBe(true);
	});

	it('leaves the root route to the page that reads the setup status', () => {
		expect(shouldRedirectToLogin({ ...signedOut, pathname: '/' })).toBe(false);
	});

	it('waits for mount and for the auth check, and never redirects public or signed-in visitors', () => {
		expect(shouldRedirectToLogin({ ...signedOut, mounted: false, pathname: '/generate' })).toBe(false);
		expect(shouldRedirectToLogin({ ...signedOut, loading: true, pathname: '/generate' })).toBe(false);
		expect(shouldRedirectToLogin({ ...signedOut, isPublicRoute: true, pathname: '/login' })).toBe(false);
		expect(shouldRedirectToLogin({ ...signedOut, isAuthenticated: true, pathname: '/generate' })).toBe(false);
	});
});
