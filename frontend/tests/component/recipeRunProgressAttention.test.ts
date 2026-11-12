import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { SetupRun } from '$lib/services/api/setup';

const { default: RecipeRunProgress } = await import('../../src/lib/components/recipes/RecipeRunProgress.svelte');
const { default: ModeInstallSteps } = await import(
	'../../src/lib/components/recipes/modeInstall/ModeInstallSteps.svelte'
);

function attempt(overrides: Record<string, unknown>) {
	return {
		step_key: 'generation.smoke',
		attempt: 1,
		status: 'action_required',
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: null,
		error_code: 'GENERATION_SMOKE_FAILED',
		safe_error_detail: "The test didn't finish: it took longer than 5 minutes.",
		safe_suggested_action: 'Make a picture in Generate to try again.',
		safe_technical_detail: 'timed out waiting for completion after 300s',
		started_at: null,
		finished_at: '2026-11-12T10:00:00Z',
		...overrides
	};
}

function run(overrides: Record<string, unknown> = {}, steps: unknown[] = []): SetupRun {
	return {
		id: 'run1',
		recipe_id: 'r',
		recipe_version: 1,
		scope: 'instance',
		mode: 'onboarding',
		status: 'completed',
		current_step: 'generation.smoke',
		safe_input: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: null,
		updated_at: null,
		completed_at: null,
		steps,
		attempts: [],
		...overrides
	} as unknown as SetupRun;
}

const smokeStep = (status: string, attempts: unknown[]) => ({
	step_key: 'generation.smoke',
	title: 'Test picture',
	kind: 'generation.smoke',
	ordinal: 1,
	status,
	attempts
});

const actions = { applyAction: async () => run(), grantConsent: async () => run() };

let mounted: Record<string, any> | null = null;

function render(component: any, props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	mounted = mount(component, { target, props });
	flushSync();
	return target;
}

afterEach(() => {
	if (mounted) unmount(mounted);
	mounted = null;
	document.body.innerHTML = '';
});

describe('RecipeRunProgress step that needs attention', () => {
	it('explains a finished step that needs attention like a failed one, with the raw text behind Details', () => {
		const target = render(RecipeRunProgress, {
			run: run({}, [smokeStep('action_required', [attempt({})])]),
			actions,
			onRunUpdated: () => {}
		});
		const text = target.textContent ?? '';
		expect(text).toContain('Needs attention');
		expect(text).toContain("Why it didn't finish");
		expect(text).toContain("The test didn't finish: it took longer than 5 minutes.");
		expect(text).toContain('Make a picture in Generate to try again.');
		const nested = target.querySelector('details details') as HTMLDetailsElement;
		expect(nested.querySelector('summary')?.textContent?.trim()).toBe('Details');
		expect(nested.textContent).toContain('timed out waiting for completion after 300s');
		expect(nested.open).toBe(false);
	});

	it('still explains an ordinary failed step', () => {
		const target = render(RecipeRunProgress, {
			run: run({ status: 'failed' }, [
				smokeStep('failed', [attempt({ status: 'failed', safe_error_detail: "Downloading 'A' failed.", safe_technical_detail: 'HTTP 403' })])
			]),
			actions,
			onRunUpdated: () => {}
		});
		expect(target.textContent).toContain("Why it didn't finish");
		expect(target.textContent).toContain("Downloading 'A' failed.");
		expect(target.textContent).toContain('HTTP 403');
	});
});

describe('RecipeRunProgress interrupted run', () => {
	const interrupted = run(
		{
			status: 'failed',
			error_code: 'RUN_INTERRUPTED',
			safe_error_detail: 'This step was interrupted by a restart. Retry it to pick up where it left off.'
		},
		[]
	);

	it('says it was interrupted, that downloads are kept, and offers Continue', () => {
		const target = render(RecipeRunProgress, { run: interrupted, actions, onRunUpdated: () => {} });
		const text = target.textContent ?? '';
		expect(text).toContain('Setup was interrupted');
		expect(text).toContain('Files already downloaded are kept.');
		expect(text).not.toContain("Setup couldn't finish");
		const labels = Array.from(target.querySelectorAll('button')).map((b) => b.textContent?.trim());
		expect(labels).toContain('Continue');
		expect(labels).not.toContain('Try again');
	});

	it('keeps the old wording for any other failure', () => {
		const target = render(RecipeRunProgress, {
			run: run({ status: 'failed', error_code: 'ARTIFACT_DOWNLOAD_FAILED', safe_error_detail: "Downloading 'A' failed." }, []),
			actions,
			onRunUpdated: () => {}
		});
		expect(target.textContent).toContain("Setup couldn't finish");
		expect(Array.from(target.querySelectorAll('button')).map((b) => b.textContent?.trim())).toContain('Try again');
	});
});

describe('ModeInstallSteps', () => {
	const groups = [
		{
			stepKey: 'generation.smoke',
			title: 'Test picture',
			kind: 'generation.smoke',
			status: 'action_required',
			latest: {
				status: 'action_required',
				safe_error_detail: "The test didn't finish: it took longer than 5 minutes.",
				safe_suggested_action: 'Make a picture in Generate to try again.'
			}
		}
	];

	it('gives a step that needs attention a warning state, not the active one', () => {
		const target = render(ModeInstallSteps, { groups, plan: null, modeCount: 0, progress: null });
		const row = target.querySelector('[data-mode-step="generation.smoke"]') as HTMLElement;
		expect(row.getAttribute('data-step-state')).toBe('attention');
		expect(row.textContent).toContain("The test didn't finish: it took longer than 5 minutes.");
		expect(row.querySelector('.text-warning')).not.toBeNull();
		expect(row.querySelector('.text-danger')).toBeNull();
	});
});
