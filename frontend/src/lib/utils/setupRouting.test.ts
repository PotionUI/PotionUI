import { describe, it, expect } from 'vitest';
import {
	decideRootDestination,
	canDecideRootDestination,
	shouldBlockRegistration,
	shouldShowRegisterLink
} from './setupRouting';
import type { SetupStatus } from '$lib/services/api/setup';

function status(overrides: Partial<SetupStatus> = {}): SetupStatus {
	return {
		needs_owner: false,
		registration_open: false,
		claim_requires_token: false,
		...overrides
	};
}

describe('decideRootDestination', () => {
	it('routes to the claim screen when the instance needs an owner', () => {
		expect(decideRootDestination(status({ needs_owner: true }), false)).toBe('/setup/claim');
	});

	it('routes to the claim screen even if a session is somehow present', () => {
		expect(decideRootDestination(status({ needs_owner: true }), true)).toBe('/setup/claim');
	});

	it('falls back to /generate for an authenticated user once claimed', () => {
		expect(decideRootDestination(status({ needs_owner: false }), true)).toBe('/generate');
	});

	it('falls back to /login for an unauthenticated user once claimed', () => {
		expect(decideRootDestination(status({ needs_owner: false }), false)).toBe('/login');
	});

	it('fails soft into auth-only routing when the status fetch errored', () => {
		expect(decideRootDestination(null, true)).toBe('/generate');
		expect(decideRootDestination(null, false)).toBe('/login');
	});
});

describe('canDecideRootDestination', () => {
	it('is decidable immediately when the instance needs an owner, even mid auth-check', () => {
		expect(canDecideRootDestination(status({ needs_owner: true }), true)).toBe(true);
	});

	it('waits for auth to resolve when the instance is already claimed', () => {
		expect(canDecideRootDestination(status({ needs_owner: false }), true)).toBe(false);
		expect(canDecideRootDestination(status({ needs_owner: false }), false)).toBe(true);
	});

	it('waits for auth to resolve when the status fetch errored', () => {
		expect(canDecideRootDestination(null, true)).toBe(false);
		expect(canDecideRootDestination(null, false)).toBe(true);
	});
});

describe('shouldBlockRegistration', () => {
	it('blocks once the instance has an owner and registration is closed', () => {
		expect(shouldBlockRegistration(status({ needs_owner: false, registration_open: false }))).toBe(true);
	});

	it('does not block while registration is open', () => {
		expect(shouldBlockRegistration(status({ needs_owner: false, registration_open: true }))).toBe(false);
	});

	it('does not block while the instance still needs an owner', () => {
		expect(shouldBlockRegistration(status({ needs_owner: true, registration_open: true }))).toBe(false);
	});

	it('does not block when the status fetch errored (preserve today\'s behavior)', () => {
		expect(shouldBlockRegistration(null)).toBe(false);
	});
});

describe('shouldShowRegisterLink', () => {
	it('hides the link once the instance has an owner and registration is closed', () => {
		expect(shouldShowRegisterLink(status({ needs_owner: false, registration_open: false }))).toBe(false);
	});

	it('shows the link while registration is open', () => {
		expect(shouldShowRegisterLink(status({ needs_owner: false, registration_open: true }))).toBe(true);
	});

	it('shows the link while the instance still needs an owner', () => {
		expect(shouldShowRegisterLink(status({ needs_owner: true, registration_open: false }))).toBe(true);
	});

	it('fails soft into showing the link when the status fetch errored', () => {
		expect(shouldShowRegisterLink(null)).toBe(true);
	});
});
