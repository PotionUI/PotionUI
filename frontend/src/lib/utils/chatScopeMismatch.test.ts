import { describe, it, expect } from 'vitest';
import { isScopeMismatched, shouldShowScopeMismatch, type ScopeDismissal } from './chatScopeMismatch';

describe('isScopeMismatched', () => {
	it('is false when the session mode matches the route mode', () => {
		expect(isScopeMismatched('models', 'models')).toBe(false);
	});

	it('is true when they differ', () => {
		expect(isScopeMismatched('models', 'generation')).toBe(true);
	});
});

describe('shouldShowScopeMismatch', () => {
	it('does not show when modes match, regardless of dismissal state', () => {
		expect(shouldShowScopeMismatch('models', 'models', 's1', null)).toBe(false);
	});

	it('shows on a fresh mismatch with no prior dismissal', () => {
		expect(shouldShowScopeMismatch('models', 'generation', 's1', null)).toBe(true);
	});

	it('is silenced by a dismissal matching the exact (sessionId, routeMode) pairing', () => {
		const dismissed: ScopeDismissal = { sessionId: 's1', routeMode: 'generation' };
		expect(shouldShowScopeMismatch('models', 'generation', 's1', dismissed)).toBe(false);
	});

	it('reinstates when the session changes but the route mode does not', () => {
		const dismissed: ScopeDismissal = { sessionId: 's1', routeMode: 'generation' };
		expect(shouldShowScopeMismatch('models', 'generation', 's2', dismissed)).toBe(true);
	});

	it('reinstates when the route mode changes but the session does not', () => {
		const dismissed: ScopeDismissal = { sessionId: 's1', routeMode: 'generation' };
		expect(shouldShowScopeMismatch('models', 'phrasebook', 's1', dismissed)).toBe(true);
	});
});
