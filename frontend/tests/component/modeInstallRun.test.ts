import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';
import { modesRequest } from '../../src/lib/components/recipes/modeInstall/modeInstallFixtures';
import type { SetupRun } from '../../src/lib/services/api/setup';

vi.mock('$lib/services/api/index', () => ({
	api: {
		planRecipe: vi.fn(),
		getGenerationImageURL: vi.fn(() => ''),
		getModeRemovalPreview: vi.fn(),
		removeModeFiles: vi.fn()
	}
}));
vi.mock('$lib/components/assignment/AssignmentCard.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));

const { api } = await import('$lib/services/api/index');
const { default: RecipeRunProgress } = await import('../../src/lib/components/recipes/RecipeRunProgress.svelte');
const { default: ModeInstallDownloads } = await import('../../src/lib/components/recipes/modeInstall/ModeInstallDownloads.svelte');
const { default: ModeRemoveModal } = await import('../../src/lib/components/recipes/modeInstall/ModeRemoveModal.svelte');
const { default: RecipeModesSection } = await import('../../src/routes/admin/components/recipes/RecipeModesSection.svelte');
const { tabsStore } = await import('../../src/lib/stores/tabs');

const GB = 1_000_000_000;

function attempt(stepKey: string, status: string, output: Record<string, unknown> | null, extra: Record<string, unknown> = {}) {
	return {
		step_key: stepKey,
		attempt: 1,
		status,
		progress_current: null,
		progress_total: null,
		progress_unit: null,
		safe_output: output,
		error_code: null,
		safe_error_detail: null,
		safe_suggested_action: null,
		started_at: null,
		finished_at: null,
		...extra
	};
}

function step(key: string, kind: string, ordinal: number, status: string, attempts: unknown[]) {
	return { step_key: key, title: key, kind, ordinal, status, attempts };
}

function run(status: string, steps: unknown[], safeInput: Record<string, unknown> | null = null): SetupRun {
	return {
		id: 'run1',
		recipe_id: 'modes-ltx-2-5',
		recipe_version: 1,
		scope: 'admin',
		mode: 'admin',
		status,
		current_step: null,
		safe_input: safeInput,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: null,
		updated_at: null,
		completed_at: null,
		steps,
		attempts: []
	} as unknown as SetupRun;
}

const consentRun = () =>
	run(
		'awaiting_consent',
		[step('modes.plan', 'modes.plan', 0, 'awaiting_consent', [attempt('modes.plan', 'awaiting_consent', { consent_request: modesRequest() })])],
		{ preselect_modes: ['cinemagraph'] }
	);

let instance: ReturnType<typeof mount> | null = null;

function render(runValue: SetupRun, actions: unknown, onRunUpdated = vi.fn()) {
	const target = document.body.appendChild(document.createElement('div'));
	instance = mount(RecipeRunProgress, { target, props: { run: runValue, actions: actions as never, onRunUpdated } });
	flushSync();
	return { target, onRunUpdated };
}

const sheetCancel = () =>
	Array.from(document.body.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Esc')) as HTMLButtonElement;

const button = (label: string) =>
	Array.from(document.body.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim().startsWith(label)) as HTMLButtonElement | undefined;

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	document.body.style.overflow = '';
});

describe('RecipeRunProgress with a modes run', () => {
	it('opens the mode sheet for a modes consent instead of the artifact list, with the run preselection', () => {
		const actions = { applyAction: vi.fn(), grantConsent: vi.fn() };
		const { target } = render(consentRun(), actions);
		expect(target.querySelector('[data-mode-consent-gate]')).not.toBeNull();
		expect(document.body.querySelector('[data-mode-install-sheet]')).not.toBeNull();
		expect(document.body.textContent).not.toContain('Approve and download');
		expect(document.body.querySelector<HTMLInputElement>('[data-mode-row="motion"] input')!.checked).toBe(false);
		expect(document.body.querySelector<HTMLInputElement>('[data-mode-row="cinemagraph"] input')!.checked).toBe(true);
	});

	it('grants consent with the picked modes, extras and target through the run actions', async () => {
		const updated = run('running', []);
		const actions = { applyAction: vi.fn(), grantConsent: vi.fn().mockResolvedValue(updated) };
		const { onRunUpdated } = render(consentRun(), actions);
		button('Install 1 mode')!.click();
		await settle();
		expect(actions.grantConsent).toHaveBeenCalledWith(
			'run1',
			'modes.plan',
			{},
			{ modes: ['video', 'cinemagraph'], extras: [], target: 'native' }
		);
		expect(onRunUpdated).toHaveBeenCalledWith(updated);
	});

	it('shows the failure and keeps the sheet open when the grant is rejected', async () => {
		const actions = { applyAction: vi.fn(), grantConsent: vi.fn().mockRejectedValue(new Error('Not enough space')) };
		render(consentRun(), actions);
		button('Install 1 mode')!.click();
		await settle();
		expect(document.body.querySelector('[data-mode-install-error]')).not.toBeNull();
		expect(document.body.querySelector('[data-mode-install-sheet]')).not.toBeNull();
	});

	it('closing the sheet leaves a card with Review and Cancel setup; Cancel cancels the run', async () => {
		const actions = { applyAction: vi.fn().mockResolvedValue(run('cancelled', [])), grantConsent: vi.fn() };
		const { target } = render(consentRun(), actions);
		sheetCancel().click();
		flushSync();
		expect(document.body.querySelector('[data-mode-install-sheet]')).toBeNull();
		expect(target.querySelector('[data-mode-consent-gate]')).not.toBeNull();
		button('Review and install')!.click();
		flushSync();
		expect(document.body.querySelector('[data-mode-install-sheet]')).not.toBeNull();
		sheetCancel().click();
		flushSync();
		(Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Cancel setup')) as HTMLButtonElement).click();
		await settle();
		expect(actions.applyAction).toHaveBeenCalledWith('run1', 'cancel');
	});

	it('asks the server to re-plan when the target changes', async () => {
		vi.mocked(api.planRecipe).mockResolvedValue({ consent_request: modesRequest() } as never);
		const actions = { applyAction: vi.fn(), grantConsent: vi.fn() };
		render(consentRun(), actions);
		(document.body.querySelector('[data-target="pod"]') as HTMLButtonElement).click();
		await settle();
		expect(api.planRecipe).toHaveBeenCalledWith('modes-ltx-2-5', { preselect_modes: ['cinemagraph'], target: 'pod' });
	});

	it('shows aggregate progress, parallel files and Cancel while the download runs', async () => {
		const files = [
			{ slot_id: 'a', display_name: 'Cinemagraph LoRA', filename: 'a', size_bytes: 200_000_000, downloaded_bytes: 200_000_000, status: 'completed' },
			{ slot_id: 'b', display_name: 'Motion patch', filename: 'b', size_bytes: 2_600_000_000, downloaded_bytes: 1_300_000_000, status: 'downloading' },
			{ slot_id: 'c', display_name: 'Face refiner', filename: 'c', size_bytes: 700_000_000, downloaded_bytes: 0, status: 'pending' }
		];
		const running = run('running', [
			step('modes.plan', 'modes.plan', 0, 'succeeded', [
				attempt('modes.plan', 'awaiting_consent', { consent_request: modesRequest() }),
				attempt('modes.plan', 'succeeded', { plan: { preset_id: 'p1', modes: ['video', 'cinemagraph'], bytes: 3, files: [1, 2, 3] } })
			]),
			step('artifacts.fetch', 'artifacts.fetch', 1, 'running', [
				attempt('artifacts.fetch', 'running', { files, files_done: 1, files_total: 3, speed_bytes_per_sec: 58_000_000, eta_seconds: 45 }, { progress_current: 1_500_000_000, progress_total: 3_500_000_000, progress_unit: 'bytes' })
			])
		]);
		const actions = { applyAction: vi.fn().mockResolvedValue(run('cancelled', [])), grantConsent: vi.fn() };
		const { target } = render(running, actions);
		const panel = target.querySelector('[data-mode-downloads]')!;
		expect(panel.textContent).toContain('58 MB/s');
		expect(panel.textContent).toContain('about 45 s left');
		expect(panel.querySelector('[data-download-file="a"]')!.textContent).toContain('Done');
		expect(panel.querySelector('[data-download-file="b"]')!.textContent).toContain('50%');
		expect(panel.querySelector('[data-download-file="c"]')!.textContent).toContain('Waiting');
		expect(target.textContent).toContain('Installing 1 mode');
		expect(target.querySelector('[data-mode-step="modes.plan"]')!.textContent).toContain('1 mode, 3 files, nothing duplicated');
		expect(target.querySelector('[data-mode-step="modes.plan"]')!.getAttribute('data-step-state')).toBe('done');
		const fetchStep = target.querySelector('[data-mode-step="artifacts.fetch"]')!;
		expect(fetchStep.getAttribute('data-step-state')).toBe('active');
		expect(fetchStep.textContent).toContain('1 of 3 running');
		(Array.from(panel.querySelectorAll('button')).find((b) => b.textContent?.includes('Cancel download')) as HTMLButtonElement).click();
		await settle();
		expect(actions.applyAction).toHaveBeenCalledWith('run1', 'cancel');
	});

	it('finishes with a Ready card and a Try it link that opens Generate on that mode', async () => {
		const consent = attempt('modes.plan', 'awaiting_consent', { consent_request: modesRequest() });
		const done = run('completed', [
			step('modes.plan', 'modes.plan', 0, 'succeeded', [
				consent,
				attempt('modes.plan', 'succeeded', { plan: { preset_id: 'p1', modes: ['video', 'cinemagraph', 'motion'], bytes: 5 * GB, files: [1, 2] } })
			])
		]);
		const addTab = vi.spyOn(tabsStore, 'addTabWithData');
		const { target } = render(done, { applyAction: vi.fn(), grantConsent: vi.fn() });
		const card = target.querySelector('[data-mode-install-done]')!;
		expect(card.textContent).toContain('Ready: Cinemagraph, Motion transfer');
		expect(card.querySelectorAll('[data-try-mode]')).toHaveLength(2);
		expect(card.textContent).toContain('4.7 GB downloaded and checked');
		expect(target.textContent).toContain('Installed 2 modes');
		expect(target.querySelector('[data-mode-access-heading]')!.textContent).toContain('Who can use LTX-2.5');
		expect(target.querySelector('[data-mode-access-heading]')!.textContent).toContain('The new modes are open to anyone who already has the preset.');
		(card.querySelector('[data-try-mode="motion"] button') as HTMLButtonElement).click();
		expect(addTab).toHaveBeenCalledWith('Motion transfer', { selectedPreset: 'p1', selectedMode: 'motion' });
		expect(generationTabs(get(tabsStore).tabs).some((tab) => tab.selectedMode === 'motion')).toBe(true);
	});
});

describe('wizard consent', () => {
	function wizardRun(): SetupRun {
		const request = modesRequest();
		request.modes[0] = { ...request.modes[0], ready: false, slot_ids: ['model', 'effect_lora@cinemagraph'] };
		return run(
			'awaiting_consent',
			[step('modes.plan', 'modes.plan', 0, 'awaiting_consent', [attempt('modes.plan', 'awaiting_consent', { consent_request: request })])]
		);
	}

	function renderGuided(actions: unknown) {
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(RecipeRunProgress, {
			target,
			props: { run: wizardRun(), actions: actions as never, onRunUpdated: vi.fn(), guided: true }
		});
		flushSync();
		return target;
	}

	it('keeps the default path to one click and hides the other modes under Add more modes', async () => {
		const actions = { applyAction: vi.fn(), grantConsent: vi.fn().mockResolvedValue(run('running', [])) };
		const target = renderGuided(actions);
		expect(target.querySelector('[data-mode-wizard-consent]')).not.toBeNull();
		expect(document.body.querySelector('[data-mode-install-sheet]')).toBeNull();
		const more = target.querySelector<HTMLDetailsElement>('[data-add-more-modes]')!;
		expect(more.open).toBe(false);
		expect(more.textContent).toContain('Add more modes');
		expect(target.querySelector('[data-wizard-total]')!.textContent).toBe('1.9 GB');
		(Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Approve and download')) as HTMLButtonElement).click();
		await settle();
		expect(actions.grantConsent).toHaveBeenCalledWith('run1', 'modes.plan', {}, { modes: ['video'], extras: [], target: 'native' });
	});

	it('adds the ticked extra modes to the total and to the grant', async () => {
		const actions = { applyAction: vi.fn(), grantConsent: vi.fn().mockResolvedValue(run('running', [])) };
		const target = renderGuided(actions);
		target.querySelector<HTMLInputElement>('[data-wizard-mode="motion"] input')!.click();
		flushSync();
		expect(target.querySelector('[data-wizard-total]')!.textContent).toBe('4.7 GB');
		(Array.from(target.querySelectorAll('button')).find((b) => b.textContent?.includes('Approve and download')) as HTMLButtonElement).click();
		await settle();
		expect(actions.grantConsent.mock.calls[0][3].modes).toEqual(['video', 'motion']);
	});
});

describe('ModeInstallDownloads', () => {
	it('calls onCancel from the Cancel download button', () => {
		const onCancel = vi.fn();
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(ModeInstallDownloads, {
			target,
			props: {
				progress: { files: [], files_done: 0, files_total: 0, speed_bytes_per_sec: null, eta_seconds: null },
				onCancel
			}
		});
		flushSync();
		expect(target.querySelector('[data-download-speed]')).toBeNull();
		(target.querySelector('button') as HTMLButtonElement).click();
		expect(onCancel).toHaveBeenCalled();
	});
});

describe('RecipeModesSection', () => {
	const modes = [
		{ id: 'video', label: 'Video', short_description: 'Text or image to video', ready: true, size_bytes: 31 * GB, own_bytes: 0, missing_bytes: 0, removable: false, shares_with: ['extend'] },
		{ id: 'upscale', label: 'Upscale', short_description: null, ready: true, size_bytes: 2 * GB, own_bytes: 1.7 * GB, missing_bytes: 0, removable: true, shares_with: [] },
		{ id: 'cine', label: 'Cinemagraph', short_description: null, ready: false, size_bytes: 2 * GB, own_bytes: 0, missing_bytes: 211_288_064, removable: false, shares_with: [] }
	];

	it('shows status, Remove files only where the mode owns files, and Install with its size where missing', () => {
		const onInstall = vi.fn();
		const onRemove = vi.fn();
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(RecipeModesSection, { target, props: { modes, onInstall, onRemove } });
		flushSync();
		expect(target.querySelector('[data-recipe-mode="video"]')!.textContent).toContain('Shares extend files'.replace('extend', 'extend'));
		expect(target.querySelector('[data-recipe-mode="video"]')!.textContent).not.toContain('Remove files');
		const upscale = target.querySelector('[data-recipe-mode="upscale"]')!;
		expect(upscale.textContent).toContain('Ready');
		(Array.from(upscale.querySelectorAll('button')).find((b) => b.textContent?.includes('Remove files')) as HTMLButtonElement).click();
		expect(onRemove).toHaveBeenCalledWith(modes[1]);
		const cine = target.querySelector('[data-recipe-mode="cine"]')!;
		expect(cine.textContent).toContain('Not installed');
		const install = Array.from(cine.querySelectorAll('button')).find((b) => b.textContent?.includes('Install')) as HTMLButtonElement;
		expect(install.textContent).toContain('202 MB');
		install.click();
		expect(onInstall).toHaveBeenCalledWith(modes[2]);
	});
});

describe('ModeRemoveModal', () => {
	const preview = {
		preset_id: 'p1',
		mode: 'upscale',
		label: 'Upscale',
		target: 'native',
		delete: [{ model_id: 'd1', label: 'Spatial upscaler', filename: 'ltx-upscaler.safetensors', size_bytes: 1.7 * GB }],
		keep: [
			{ model_id: 'k1', label: 'Main model', filename: 'm.safetensors', size_bytes: 20 * GB, reason_code: 'shared', reason: 'Used by Video and 9 other modes' },
			{ model_id: 'k2', label: 'Upscaler', filename: 'ltx-upscale-custom.safetensors', size_bytes: 1.2 * GB, reason_code: 'outside_models_folder', reason: 'You added this yourself, outside the models folder' }
		],
		frees_bytes: 1.7 * GB,
		clears_defaults: [{ field: 'upscale_model' }]
	};

	function open(onRemoved = vi.fn(), onClose = vi.fn()) {
		vi.mocked(api.getModeRemovalPreview).mockResolvedValue(preview as never);
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(ModeRemoveModal, {
			target,
			props: { recipeId: 'modes-ltx-2-5', modeId: 'upscale', modeLabel: 'Upscale', onRemoved, onClose }
		});
		flushSync();
		return { onRemoved, onClose };
	}

	it('always lists what is deleted and what is kept, with reasons', async () => {
		open();
		await settle();
		flushSync();
		const deleted = document.body.querySelector('[data-remove-deleted]')!;
		expect(deleted.textContent).toContain('Spatial upscaler');
		expect(deleted.textContent).toContain('ltx-upscaler.safetensors');
		const kept = document.body.querySelector('[data-remove-kept]')!;
		expect(kept.textContent).toContain('Used by Video and 9 other modes');
		expect(kept.textContent).toContain('ltx-upscale-custom.safetensors');
		expect(kept.textContent).toContain('You added this yourself');
		expect(kept.textContent).toContain('default file choice is cleared');
		expect(document.body.textContent).toContain('Remove Upscale files?');
		expect(document.body.querySelector('[data-remove-frees]')!.textContent).toContain('Frees 1.6 GB');
		expect(button('Remove 1 file')!.disabled).toBe(false);
		expect(button('Keep files')!.disabled).toBe(false);
	});

	it('removes exactly the previewed ids and reports the freed bytes', async () => {
		vi.mocked(api.removeModeFiles).mockResolvedValue({ deleted: [], kept: [], freed_bytes: 1.7 * GB } as never);
		const { onRemoved } = open();
		await settle();
		flushSync();
		button('Remove 1 file')!.click();
		await settle();
		expect(api.removeModeFiles).toHaveBeenCalledWith('modes-ltx-2-5', 'upscale', ['d1']);
		expect(onRemoved).toHaveBeenCalledWith(1.7 * GB);
	});

	it('keeps the modal open and says why when removal fails', async () => {
		vi.mocked(api.removeModeFiles).mockRejectedValue(new Error('Nothing to remove'));
		const { onRemoved } = open();
		await settle();
		flushSync();
		button('Remove 1 file')!.click();
		await settle();
		flushSync();
		expect(onRemoved).not.toHaveBeenCalled();
		expect(document.body.querySelector('[data-mode-remove]')).not.toBeNull();
	});

	it('disables the confirm button when there is nothing of its own to delete', async () => {
		vi.mocked(api.getModeRemovalPreview).mockResolvedValue({ ...preview, delete: [], frees_bytes: 0 } as never);
		const target = document.body.appendChild(document.createElement('div'));
		instance = mount(ModeRemoveModal, {
			target,
			props: { recipeId: 'r', modeId: 'upscale', modeLabel: 'Upscale', onRemoved: vi.fn(), onClose: vi.fn() }
		});
		await settle();
		flushSync();
		expect(button('Nothing to remove')!.disabled).toBe(true);
	});
});
