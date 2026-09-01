import { describe, it, expect } from 'vitest';
import { resolveAuthLayoutMode, type AuthLayoutState } from './authLayout';

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

	it('never falls back to app before mount even if authenticated', () => {
		expect(
			resolveAuthLayoutMode(state({ mounted: false, isAuthenticated: true, isPublicRoute: false }))
		).toBe('public');
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
