import { describe, it, expect } from 'vitest';
import { normalizeLoginProviders } from './loginProviders';

describe('normalizeLoginProviders', () => {
	it('returns an empty list when there are no providers', () => {
		expect(normalizeLoginProviders([])).toEqual([]);
	});

	it('returns each provider in order', () => {
		const raw = [
			{ id: 'acme-sso', label: 'Acme SSO', start_path: '/api/plugins/acme-sso/start' },
			{ id: 'globex-oidc', label: 'Globex OIDC', start_path: '/api/plugins/globex-oidc/start' }
		];

		expect(normalizeLoginProviders(raw)).toEqual([
			{ id: 'acme-sso', label: 'Acme SSO', start_path: '/api/plugins/acme-sso/start' },
			{ id: 'globex-oidc', label: 'Globex OIDC', start_path: '/api/plugins/globex-oidc/start' }
		]);
	});

	it('returns an empty list for null input', () => {
		expect(normalizeLoginProviders(null)).toEqual([]);
	});

	it('returns an empty list for undefined input', () => {
		expect(normalizeLoginProviders(undefined)).toEqual([]);
	});

	it('returns an empty list for non-array input', () => {
		expect(normalizeLoginProviders({ id: 'acme-sso' })).toEqual([]);
		expect(normalizeLoginProviders('acme-sso')).toEqual([]);
	});

	it('filters out entries missing id, label, or start_path', () => {
		const raw = [
			{ id: 'acme-sso', label: 'Acme SSO', start_path: '/api/plugins/acme-sso/start' },
			{ label: 'Missing id', start_path: '/api/plugins/no-id/start' },
			{ id: 'missing-label', start_path: '/api/plugins/no-label/start' },
			{ id: 'missing-start-path', label: 'Missing start path' },
			null,
			undefined,
			'not-an-object',
			42
		];

		expect(normalizeLoginProviders(raw)).toEqual([
			{ id: 'acme-sso', label: 'Acme SSO', start_path: '/api/plugins/acme-sso/start' }
		]);
	});
});
