import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { SetupRun, SetupRunStepView, SetupStepAttempt } from '$lib/services/api/setup';
import type { RecipeRunActions } from '$lib/components/recipes/runActions';

const { default: RecipeRunProgress } = await import(
	'../../src/lib/components/recipes/RecipeRunProgress.svelte'
);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function attempt(overrides: Partial<SetupStepAttempt> = {}): SetupStepAttempt {
	return {
		step_key: 'artifacts.plan',
		attempt: 1,
		status: 'awaiting_consent',
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		safe_suggested_action: null,
		started_at: null,
		finished_at: null,
		...overrides
	};
}

function step(overrides: Partial<SetupRunStepView> = {}): SetupRunStepView {
	return {
		step_key: 'artifacts.plan',
		title: 'Download artifacts',
		kind: 'artifacts.plan',
		ordinal: 0,
		status: 'awaiting_consent',
		attempts: [attempt()],
		...overrides
	};
}

function run(overrides: Partial<SetupRun> = {}): SetupRun {
	return {
		id: 'run-1',
		recipe_id: 'krea2-starter',
		recipe_version: 1,
		scope: 'onboarding',
		mode: 'onboarding',
		status: 'awaiting_consent',
		current_step: 'artifacts.plan',
		safe_input: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: null,
		updated_at: null,
		completed_at: null,
		steps: [],
		attempts: [],
		...overrides
	};
}

function slottedConsentRequest() {
	return {
		artifacts: [
			{
				id: 'diffusion_model',
				display_name: 'Balanced',
				size_bytes: 13_140_000_000,
				kind: 'checkpoint',
				gated: false,
				license_url: null
			}
		],
		total_bytes: 13_140_000_000,
		gpu: {
			generation: 'ada',
			generation_label: 'RTX 40-series (Ada)',
			name: 'GeForce RTX 4090',
			vram_gb: 24,
			compute_capability: '8.9',
			fast_precisions: ['bf16', 'fp16', 'int8', 'fp8']
		},
		slots: [
			{
				id: 'diffusion_model',
				label: 'Diffusion model',
				kind: 'checkpoint',
				model_type: 'diffusion_model',
				required: true,
				recommended_variant_id: 'balanced',
				reason: 'Fits your 24 GB',
				variants: [
					{
						id: 'best',
						label: 'Best quality',
						precision: 'bf16',
						filename: 'krea2_best_bf16.safetensors',
						size_bytes: 26_280_000_000,
						installed: false,
						gated: false,
						license_url: null,
						uploader: 'Comfy-Org',
						source: 'huggingface',
						repo_id: 'Comfy-Org/Krea-2',
						source_url: 'https://huggingface.co/Comfy-Org/Krea-2',
						is_recipe_default: false,
						fast: false,
						recommended: false,
						note: null
					},
					{
						id: 'balanced',
						label: 'Balanced',
						precision: 'fp8',
						filename: 'krea2_balanced_fp8.safetensors',
						size_bytes: 13_140_000_000,
						installed: false,
						gated: false,
						license_url: null,
						uploader: 'Comfy-Org',
						source: 'huggingface',
						repo_id: 'Comfy-Org/Krea-2',
						source_url: 'https://huggingface.co/Comfy-Org/Krea-2',
						is_recipe_default: true,
						fast: true,
						recommended: true,
						note: null
					}
				]
			}
		]
	};
}

function flatConsentRequest() {
	return {
		artifacts: [
			{
				id: 'a1',
				display_name: 'SDXL checkpoint',
				size_bytes: 6_900_000_000,
				kind: 'checkpoint',
				gated: false,
				license_url: null
			}
		],
		total_bytes: 6_900_000_000
	};
}

function mountProgress(theRun: SetupRun, actions: RecipeRunActions) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(RecipeRunProgress, {
		target,
		props: { run: theRun, actions, onRunUpdated: vi.fn() }
	});
	flushSync();
}

function approveButton(): HTMLButtonElement {
	const button = Array.from(target.querySelectorAll('button')).find((el) =>
		el.textContent?.trim().includes('Approve and download')
	) as HTMLButtonElement | undefined;
	expect(button, 'expected an Approve and download button').toBeTruthy();
	return button!;
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('RecipeRunProgress consent gate', () => {
	it('sends the overridden selections when approving a slotted consent request', async () => {
		const grantConsent = vi.fn().mockResolvedValue(run({ status: 'running' }));
		const actions: RecipeRunActions = { applyAction: vi.fn(), grantConsent };
		const theRun = run({ steps: [step({ attempts: [attempt({ safe_output: { consent_request: slottedConsentRequest() } })] })] });
		mountProgress(theRun, actions);

		const bestOption = target.querySelector('[data-consent-variant="best"]') as HTMLElement;
		expect(bestOption).not.toBeNull();
		bestOption.dispatchEvent(new MouseEvent('click', { bubbles: true }));
		flushSync();

		approveButton().click();
		await Promise.resolve();
		flushSync();

		expect(grantConsent).toHaveBeenCalledWith('run-1', 'artifacts.plan', { diffusion_model: 'best' });
	});

	it('falls back to the flat artifact list and empty selections when the run has no slots', async () => {
		const grantConsent = vi.fn().mockResolvedValue(run({ status: 'running' }));
		const actions: RecipeRunActions = { applyAction: vi.fn(), grantConsent };
		const theRun = run({ steps: [step({ attempts: [attempt({ safe_output: { consent_request: flatConsentRequest() } })] })] });
		mountProgress(theRun, actions);

		expect(target.querySelector('[role="radiogroup"]')).toBeNull();
		expect(target.textContent).toContain('SDXL checkpoint');

		approveButton().click();
		await Promise.resolve();
		flushSync();

		expect(grantConsent).toHaveBeenCalledWith('run-1', 'artifacts.plan', {});
	});
});
