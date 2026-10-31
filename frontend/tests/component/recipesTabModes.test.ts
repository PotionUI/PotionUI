import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { RecipeSummary } from '$lib/services/api/recipes';
import { settle } from './helpers/settle';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			listRecipes: vi.fn(),
			getRecipe: vi.fn(),
			getRecipeReadiness: vi.fn(),
			listRecipeRuns: vi.fn(),
			createRecipeRun: vi.fn(),
			startRecipeModesRun: vi.fn(),
			getRecipeRun: vi.fn(),
			planRecipe: vi.fn(),
			getModeRemovalPreview: vi.fn(),
			removeModeFiles: vi.fn()
		}
	};
});

const api = await import('$lib/services/api/index');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: RecipesTab } = await import('../../src/routes/admin/components/RecipesTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

const GB = 1024 ** 3;

function recipe(overrides: Partial<RecipeSummary> = {}): RecipeSummary {
	return {
		id: 'ltx25-starter',
		name: 'LTX-2.5 Starter',
		summary: 'Starter',
		description: '',
		engine: 'native',
		category: 'video',
		artifact_count: 3,
		total_download_bytes: 18 * GB,
		last_completed_at: null,
		source: 'marketplace',
		plugin_id: null,
		step_count: 4,
		presets: [],
		...overrides
	};
}

const MODES_RECIPE = recipe({
	id: 'modes-ltx-2-5',
	name: 'LTX-2.5 modes',
	summary: 'Pick the video modes you want and get their files.',
	source: 'derived',
	artifact_count: 0,
	total_download_bytes: 5 * GB,
	presets: [{ id: 'p1', name: 'LTX-2.5', cover_url: null, installed: true }],
	modes_total: 12,
	modes_ready: 8,
	missing_bytes: 5 * GB,
	installed_bytes: 35.6 * GB
});

const DETAIL_MODES = [
	{ id: 'video', label: 'Video', short_description: 'Text or image to video', ready: true, size_bytes: 31 * GB, own_bytes: 0, missing_bytes: 0, removable: false, shares_with: ['extend'] },
	{ id: 'upscale', label: 'Upscale', short_description: 'Sharper video', ready: true, size_bytes: 2 * GB, own_bytes: 1.7 * GB, missing_bytes: 0, removable: true, shares_with: [] },
	{ id: 'cine', label: 'Cinemagraph', short_description: 'Loop a still', ready: false, size_bytes: 0.2 * GB, own_bytes: 0, missing_bytes: 0.2 * GB, removable: false, shares_with: [] }
];

function setup(recipes: RecipeSummary[]) {
	(api.api.listRecipes as any).mockResolvedValue({ recipes });
	(api.api.getRecipeReadiness as any).mockResolvedValue({
		overall: 'ready',
		checks: [{ area: 'content', status: 'ready', code: 'ok', message: '', action: null }]
	});
	(api.api.getRecipe as any).mockImplementation(async (id: string) => ({
		...(recipes.find((r) => r.id === id) ?? recipes[0]),
		steps: [],
		artifacts: [],
		smoke: null,
		load_errors: [],
		modes: id === 'modes-ltx-2-5' ? DETAIL_MODES : []
	}));
	(api.api.listRecipeRuns as any).mockResolvedValue({ runs: [] });
	(api.api.startRecipeModesRun as any).mockResolvedValue({
		id: 'run-1',
		recipe_id: 'modes-ltx-2-5',
		recipe_version: 1,
		scope: 'admin',
		mode: 'admin',
		status: 'running',
		current_step: null,
		safe_input: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: null,
		updated_at: null,
		completed_at: null,
		steps: [],
		attempts: []
	});
}

function mount() {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({ component: RecipesTab as never, target, props: {} });
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	document.body.innerHTML = '';
	vi.clearAllMocks();
	page.update((current) => ({ ...current, url: new URL('http://localhost/admin') }));
});

const buttonIn = (root: ParentNode, label: string) =>
	Array.from(root.querySelectorAll('button')).find((b) => (b.textContent ?? '').trim().startsWith(label)) as HTMLButtonElement | undefined;

describe('RecipesTab modes recipes', () => {
	it('groups mode recipes under Modes with a ready count and the real size, and starters below', async () => {
		setup([recipe(), MODES_RECIPE]);
		mounted = mount();
		await settle();
		const target = mounted.target;
		expect(target.querySelector('[data-recipe-group="modes"]')).not.toBeNull();
		expect(target.querySelector('[data-recipe-group="starters"]')).not.toBeNull();
		const modesGrid = target.querySelector('[role="list"][aria-label="Mode recipes"]')!;
		expect(modesGrid.textContent).toContain('LTX-2.5 modes');
		expect(modesGrid.textContent).toContain('8 of 12 modes ready');
		expect(modesGrid.textContent).toContain('35.6 GB on disk · 4 to add');
		expect(buttonIn(modesGrid, 'Install modes')!.disabled).toBe(false);
		const starters = target.querySelector('[role="list"][aria-label="Recipe catalog"]')!;
		expect(starters.textContent).toContain('LTX-2.5 Starter');
		expect(starters.textContent).not.toContain('modes ready');
	});

	it('says Manage modes when every mode is ready', async () => {
		setup([{ ...MODES_RECIPE, modes_ready: 12, installed_bytes: 41 * GB }]);
		mounted = mount();
		await settle();
		const modesGrid = mounted.target.querySelector('[role="list"][aria-label="Mode recipes"]')!;
		expect(modesGrid.textContent).toContain('All 12 modes ready');
		expect(buttonIn(modesGrid, 'Manage modes')!.disabled).toBe(false);
		expect(modesGrid.textContent).not.toContain('to add');
	});

	it('opens the recipe and starts its run when the card button is used', async () => {
		setup([MODES_RECIPE]);
		(api.api.createRecipeRun as any).mockResolvedValue({
			id: 'run-2',
			recipe_id: 'modes-ltx-2-5',
			recipe_version: 1,
			scope: 'admin',
			mode: 'admin',
			status: 'running',
			current_step: null,
			safe_input: null,
			safe_output: null,
			error_code: null,
			safe_error_detail: null,
			created_at: null,
			updated_at: null,
			completed_at: null,
			steps: [],
			attempts: []
		});
		mounted = mount();
		await settle();
		flushSync(() => buttonIn(mounted!.target, 'Install modes')!.click());
		await settle();
		expect(api.api.getRecipe).toHaveBeenCalledWith('modes-ltx-2-5');
		expect(api.api.createRecipeRun).toHaveBeenCalledWith('modes-ltx-2-5');
		expect(mounted.target.querySelector('[role="list"][aria-label="Mode recipes"]')).toBeNull();
	});

	it('shows the modes section on the detail page with per-mode actions, and preselects the clicked mode on Install', async () => {
		setup([MODES_RECIPE]);
		page.update((current) => ({ ...current, url: new URL('http://localhost/admin?id=modes-ltx-2-5') }));
		mounted = mount();
		await settle();
		const target = mounted.target;
		const section = target.querySelector('[data-recipe-modes]')!;
		expect(section).not.toBeNull();
		expect(section.textContent).toContain('Shares extend files');
		expect(section.querySelectorAll('[data-recipe-mode]')).toHaveLength(3);
		expect(target.querySelector('[data-recipe-modes-subtitle]')!.textContent).toContain('Sets up LTX-2.5 · 35.6 GB on disk · 4 to add');
		expect(buttonIn(target, 'Preview')!.disabled).toBe(false);
		flushSync(() => buttonIn(section, 'Install')!.click());
		await settle();
		expect(api.api.startRecipeModesRun).toHaveBeenCalledWith('modes-ltx-2-5', ['cine']);
	});

	it('Preview runs the dry run and shows steps and totals without starting a run', async () => {
		setup([MODES_RECIPE]);
		(api.api.planRecipe as any).mockResolvedValue({
			recipe_id: 'modes-ltx-2-5',
			steps: [
				{ key: 'modes.plan', kind: 'modes.plan', title: 'Pick modes and review downloads', onboarding_only: false, preview: { summary: '12 modes, 8 ready' } },
				{ key: 'artifacts.fetch', kind: 'artifacts.fetch', title: 'Download the files', onboarding_only: false, preview: null }
			],
			consent_request: null,
			totals: { download_bytes: 5 * GB, file_count: 4 }
		});
		page.update((current) => ({ ...current, url: new URL('http://localhost/admin?id=modes-ltx-2-5') }));
		mounted = mount();
		await settle();
		flushSync(() => buttonIn(mounted!.target, 'Preview')!.click());
		await settle();
		flushSync();
		const preview = document.body.querySelector('[data-recipe-plan-preview]')!;
		expect(api.api.planRecipe).toHaveBeenCalledWith('modes-ltx-2-5');
		expect(preview.textContent).toContain('12 modes, 8 ready');
		expect(preview.textContent).toContain('5.0 GB · 4 files');
		expect(api.api.startRecipeModesRun).not.toHaveBeenCalled();
	});

	it('opens the remove modal from Remove files and refreshes the detail after removal', async () => {
		setup([MODES_RECIPE]);
		(api.api.getModeRemovalPreview as any).mockResolvedValue({
			preset_id: 'p1',
			mode: 'upscale',
			label: 'Upscale',
			target: 'native',
			delete: [{ model_id: 'd1', label: 'Spatial upscaler', filename: 'u.safetensors', size_bytes: 1.7 * GB }],
			keep: [],
			frees_bytes: 1.7 * GB,
			clears_defaults: []
		});
		(api.api.removeModeFiles as any).mockResolvedValue({ deleted: [], kept: [], freed_bytes: 1.7 * GB });
		page.update((current) => ({ ...current, url: new URL('http://localhost/admin?id=modes-ltx-2-5') }));
		mounted = mount();
		await settle();
		flushSync(() => buttonIn(mounted!.target.querySelector('[data-recipe-mode="upscale"]')!, 'Remove files')!.click());
		await settle();
		flushSync();
		expect(api.api.getModeRemovalPreview).toHaveBeenCalledWith('modes-ltx-2-5', 'upscale');
		const detailCalls = (api.api.getRecipe as any).mock.calls.length;
		flushSync(() => buttonIn(document.body, 'Remove 1 file')!.click());
		await settle();
		expect(api.api.removeModeFiles).toHaveBeenCalledWith('modes-ltx-2-5', 'upscale', ['d1']);
		expect((api.api.getRecipe as any).mock.calls.length).toBeGreaterThan(detailCalls);
		expect(document.body.querySelector('[data-mode-remove]')).toBeNull();
	});
});
