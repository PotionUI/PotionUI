import { describe, it, expect, afterEach, vi } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { settle } from './helpers/settle';
import { modesRequest } from '../../src/lib/components/recipes/modeInstall/modeInstallFixtures';
import type { SetupRun } from '../../src/lib/services/api/setup';

vi.mock('$lib/services/api/index', () => ({
	api: {
		startRecipeModesRun: vi.fn(),
		createRecipeRun: vi.fn(),
		getRecipeRun: vi.fn(),
		grantRecipeRunConsent: vi.fn(),
		applyRecipeRunAction: vi.fn(),
		listRecipes: vi.fn().mockResolvedValue({ recipes: [] }),
		planRecipe: vi.fn(),
		getGenerationImageURL: vi.fn(() => '')
	}
}));

const { api } = await import('$lib/services/api/index');
const { default: ModeInstallHost } = await import('../../src/lib/components/recipes/modeInstall/ModeInstallHost.svelte');
const { openModeInstall, closeModeInstall } = await import('../../src/lib/stores/modeInstall');
const { onModelInstalled } = await import('../../src/lib/stores/modelInstalls');

function run(status: string, steps: unknown[], safeInput: Record<string, unknown> = {}): SetupRun {
	return {
		id: 'run-h',
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

const awaiting = () =>
	run(
		'awaiting_consent',
		[
			{
				step_key: 'modes.plan',
				title: 'Plan',
				kind: 'modes.plan',
				ordinal: 0,
				status: 'awaiting_consent',
				attempts: [
					{
						step_key: 'modes.plan',
						attempt: 1,
						status: 'awaiting_consent',
						progress_current: null,
						progress_total: null,
						progress_unit: null,
						safe_output: { consent_request: modesRequest() },
						error_code: null,
						safe_error_detail: null,
						safe_suggested_action: null,
						started_at: null,
						finished_at: null
					}
				]
			}
		],
		{ preselect_modes: ['motion'] }
	);

let instance: ReturnType<typeof mount> | null = null;

function mountHost() {
	instance = mount(ModeInstallHost, { target: document.body.appendChild(document.createElement('div')) });
	flushSync();
}

const sheetCancel = () =>
	Array.from(document.body.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Esc')) as HTMLButtonElement;

afterEach(() => {
	closeModeInstall();
	if (instance) unmount(instance);
	instance = null;
	document.body.innerHTML = '';
	document.body.style.overflow = '';
	vi.clearAllMocks();
});

describe('ModeInstallHost', () => {
	it('renders nothing until a door asks for an install', () => {
		mountHost();
		expect(document.body.querySelector('[data-mode-install-sheet]')).toBeNull();
		expect(document.body.querySelector('[data-mode-install-host]')).toBeNull();
	});

	it('starts the derived recipe with the clicked mode and opens the sheet with it preselected', async () => {
		vi.mocked(api.startRecipeModesRun).mockResolvedValue(awaiting());
		mountHost();
		openModeInstall('modes-ltx-2-5', ['motion']);
		await settle();
		flushSync();
		expect(api.startRecipeModesRun).toHaveBeenCalledWith('modes-ltx-2-5', ['motion']);
		expect(document.body.querySelector('[data-mode-install-sheet]')).not.toBeNull();
		expect(document.body.querySelector<HTMLInputElement>('[data-mode-row="motion"] input')!.checked).toBe(true);
		expect(document.body.querySelector<HTMLInputElement>('[data-mode-row="cinemagraph"] input')!.checked).toBe(false);
	});

	it('starts a plain run when no mode was preselected', async () => {
		vi.mocked(api.createRecipeRun).mockResolvedValue(awaiting());
		mountHost();
		openModeInstall('modes-ltx-2-5');
		await settle();
		expect(api.createRecipeRun).toHaveBeenCalledWith('modes-ltx-2-5');
		expect(api.startRecipeModesRun).not.toHaveBeenCalled();
	});

	it('grants the approved modes and shows progress in place of the sheet', async () => {
		vi.mocked(api.startRecipeModesRun).mockResolvedValue(awaiting());
		vi.mocked(api.grantRecipeRunConsent).mockResolvedValue(run('running', []));
		mountHost();
		openModeInstall('modes-ltx-2-5', ['motion']);
		await settle();
		flushSync();
		const install = Array.from(document.body.querySelectorAll('button')).find((b) => (b.textContent ?? '').includes('Install 1 mode')) as HTMLButtonElement;
		install.click();
		await settle();
		flushSync();
		expect(api.grantRecipeRunConsent).toHaveBeenCalledWith('run-h', 'modes.plan', {}, { modes: ['video', 'motion'], extras: [], target: 'native' });
		expect(document.body.querySelector('[data-mode-install-sheet]')).toBeNull();
		expect(document.body.querySelector('[data-mode-install-host]')).not.toBeNull();
	});

	it('cancelling the sheet cancels the parked run so nothing is left blocking other recipes', async () => {
		vi.mocked(api.startRecipeModesRun).mockResolvedValue(awaiting());
		vi.mocked(api.applyRecipeRunAction).mockResolvedValue(run('cancelled', []));
		mountHost();
		openModeInstall('modes-ltx-2-5', ['motion']);
		await settle();
		flushSync();
		sheetCancel().click();
		await settle();
		flushSync();
		expect(api.applyRecipeRunAction).toHaveBeenCalledWith('run-h', 'cancel');
		expect(document.body.querySelector('[data-mode-install-sheet]')).toBeNull();
		expect(document.body.querySelector('[data-mode-install-host]')).toBeNull();
	});

	it('tells every listener a model was installed when the run completes', async () => {
		const listener = vi.fn();
		const off = onModelInstalled(listener);
		vi.mocked(api.startRecipeModesRun).mockResolvedValue(run('completed', []));
		mountHost();
		openModeInstall('modes-ltx-2-5', ['motion']);
		await settle();
		off();
		expect(listener).toHaveBeenCalledTimes(1);
	});

	it('shows the reason in the dialog when the run cannot start', async () => {
		vi.mocked(api.startRecipeModesRun).mockRejectedValue(new Error('boom'));
		mountHost();
		openModeInstall('modes-ltx-2-5', ['motion']);
		await settle();
		flushSync();
		expect(document.body.querySelector('[data-mode-install-host]')!.textContent).toContain("Couldn't start this install");
	});
});
