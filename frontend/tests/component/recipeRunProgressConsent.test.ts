import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { SetupRun } from '$lib/services/api/setup';

const { default: RecipeRunProgress } = await import('../../src/lib/components/recipes/RecipeRunProgress.svelte');

function consentRun(providers: Record<string, unknown>[]): SetupRun {
	const attempt = {
		step_key: 'fetch',
		attempt: 1,
		status: 'awaiting_consent',
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: {
			consent_request: {
				artifacts: [
					{ id: 'a1', display_name: 'Base model', size_bytes: 1000, kind: 'checkpoint', gated: false, license_url: null }
				],
				total_bytes: 1000,
				providers
			}
		},
		error_code: null,
		safe_error_detail: null,
		safe_suggested_action: null,
		started_at: null,
		finished_at: null
	};
	return {
		id: 'run1',
		recipe_id: 'r',
		recipe_version: 1,
		scope: 'admin',
		mode: 'apply',
		status: 'awaiting_consent',
		current_step: 'fetch',
		safe_input: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: null,
		updated_at: null,
		completed_at: null,
		steps: [{ step_key: 'fetch', title: 'Fetch models', kind: 'fetch', ordinal: 0, status: 'awaiting_consent', attempts: [attempt] }],
		attempts: []
	} as unknown as SetupRun;
}

const actions = {
	applyAction: async () => consentRun([]),
	grantConsent: async () => consentRun([])
};

let mounted: Record<string, any> | null = null;

function render(run: SetupRun) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(RecipeRunProgress, { target, props: { run, actions, onRunUpdated: () => {} } });
	flushSync();
	return target;
}

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	document.body.innerHTML = '';
});

describe('RecipeRunProgress consent gate', () => {
	it('renders the API key field and the approve button for a provider without a key', () => {
		const target = render(
			consentRun([{ id: 'civitai', name: 'CivitAI', website: '', field_name: 'api_key', configured: false }])
		);
		const field = target.querySelector('input[type="password"]') as HTMLInputElement | null;
		expect(field).not.toBeNull();
		expect(field?.value).toBe('');
		expect(target.textContent).toContain('Approve and download');
	});

	it('enables Save only once a key is typed', () => {
		const target = render(
			consentRun([{ id: 'civitai', name: 'CivitAI', website: '', field_name: 'api_key', configured: false }])
		);
		const field = target.querySelector('input[type="password"]') as HTMLInputElement;
		const save = [...target.querySelectorAll('button')].find((b) => b.textContent?.trim() === 'Save') as HTMLButtonElement;
		expect(save.disabled).toBe(true);
		field.value = 'secret';
		field.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(save.disabled).toBe(false);
	});

	it('shows no key field when the provider is already configured', () => {
		const target = render(
			consentRun([{ id: 'civitai', name: 'CivitAI', website: '', field_name: 'api_key', configured: true }])
		);
		expect(target.querySelector('input[type="password"]')).toBeNull();
		expect(target.textContent).toContain('CivitAI API key saved.');
	});
});
