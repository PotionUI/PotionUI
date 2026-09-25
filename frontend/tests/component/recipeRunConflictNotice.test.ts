// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { PresetInfo } from '$lib/types/api';
import type { RecipeSummary } from '$lib/services/api/recipes';
import type { SetupRun } from '$lib/services/api/setup';

type PageStore = Writable<{ url: URL }>;

vi.mock('$lib/services/api/index', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api/index')>('$lib/services/api/index');
	return {
		...actual,
		api: {
			...actual.api,
			listPresets: vi.fn(),
			getPreset: vi.fn(),
			getPresetConfiguration: vi.fn(),
			createRecipeRun: vi.fn(),
			getRecipeRun: vi.fn(),
			applyRecipeRunAction: vi.fn(),
			listRecipes: vi.fn(),
			getRecipe: vi.fn(),
			getRecipeReadiness: vi.fn(),
			listRecipeRuns: vi.fn()
		}
	};
});
vi.mock('$lib/services/admin-api', () => ({
	installPreset: vi.fn(),
	uninstallPreset: vi.fn()
}));
vi.mock('$app/navigation', async () => {
	const { page } = await import('$app/stores');
	const store = page as unknown as PageStore;
	return {
		goto: async (href: string) => {
			store.update((current) => ({ ...current, url: new URL(href, 'http://localhost') }));
		},
		invalidate: async () => {},
		invalidateAll: async () => {},
		preloadData: async () => {},
		preloadCode: async () => {},
		afterNavigate: () => {},
		beforeNavigate: () => {},
		pushState: () => {},
		replaceState: () => {}
	};
});

const api = await import('$lib/services/api/index');
const page = (await import('$app/stores')).page as unknown as PageStore;
const { default: PresetsTab } = await import('../../src/routes/admin/components/PresetsTab.svelte');
const { default: RecipesTab } = await import('../../src/routes/admin/components/RecipesTab.svelte');
const { createClassComponent } = await import('svelte/legacy');

const KREA_RECIPE = {
	id: 'krea2-starter',
	name: 'Krea-2 Starter',
	readiness: 'available' as const,
	total_download_bytes: 18_600_000_000
};

function preset(overrides: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id: 'krea2',
		name: 'Krea-2',
		version: '1.0.0',
		description: '',
		tags: [],
		category: 'image',
		engine: 'native',
		installed: false,
		recipes: [KREA_RECIPE],
		...overrides
	};
}

function run(overrides: Partial<SetupRun> = {}): SetupRun {
	return {
		id: 'run-1',
		recipe_id: 'krea2-starter',
		recipe_version: 1,
		scope: 'instance',
		mode: 'admin',
		status: 'pending',
		current_step: null,
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

function recipeSummary(): RecipeSummary {
	return {
		id: 'minimax-h3-starter',
		name: 'MiniMax-H3 Starter',
		summary: 'Get MiniMax-H3 running.',
		description: '',
		engine: 'native',
		category: 'video',
		artifact_count: 3,
		total_download_bytes: 18_600_000_000,
		last_completed_at: null,
		source: 'marketplace',
		plugin_id: null,
		step_count: 4,
		presets: []
	};
}

function conflictError(activeRun: { id: string; recipe_id: string; recipe_name: string; status: string }) {
	return {
		isAxiosError: true,
		message: 'Request failed with status code 409',
		response: { status: 409, data: { detail: { message: 'busy', active_run: activeRun } } }
	};
}

function setUrl(search: string) {
	page.update((current) => ({ ...current, url: new URL(`http://localhost/admin${search}`) }));
}

function mount(component: unknown) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const instance = createClassComponent({ component: component as never, target, props: {} });
	return {
		target,
		destroy: () => {
			instance.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	setUrl('');
	document.body.innerHTML = '';
});

describe('Starting a recipe while another one is active', () => {
	it('shows an inline notice with Open it / Cancel it instead of a bare 409', async () => {
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [preset()] } as never);
		vi.mocked(api.api.getPreset).mockResolvedValue({ success: true, data: preset() } as never);
		vi.mocked(api.api.createRecipeRun).mockRejectedValue(
			conflictError({
				id: 'run-9',
				recipe_id: 'minimax-h3-starter',
				recipe_name: 'MiniMax-H3 Starter',
				status: 'awaiting_consent'
			})
		);
		setUrl('?tab=presets&id=krea2');
		mounted = mount(PresetsTab);
		await settle();

		const startButton = Array.from(mounted.target.querySelectorAll('button')).find((button) =>
			button.textContent?.includes('Set up with recipe')
		)!;
		flushSync(() => startButton.click());
		await settle();

		expect(mounted.target.textContent).not.toContain('409');
		const notice = mounted.target.querySelector('[data-recipe-run-conflict]') as HTMLElement;
		expect(notice).not.toBeNull();
		expect(notice.textContent).toContain('MiniMax-H3 Starter');
		expect(notice.textContent).toContain('still running');
		expect(Array.from(notice.querySelectorAll('button, a')).some((el) => el.textContent?.trim() === 'Open it')).toBe(
			true
		);
		expect(
			Array.from(notice.querySelectorAll('button, a')).some((el) => el.textContent?.trim() === 'Cancel it')
		).toBe(true);
	});

	it('cancels the blocking run and clears the notice', async () => {
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [preset()] } as never);
		vi.mocked(api.api.getPreset).mockResolvedValue({ success: true, data: preset() } as never);
		vi.mocked(api.api.createRecipeRun).mockRejectedValue(
			conflictError({
				id: 'run-9',
				recipe_id: 'minimax-h3-starter',
				recipe_name: 'MiniMax-H3 Starter',
				status: 'running'
			})
		);
		vi.mocked(api.api.applyRecipeRunAction).mockResolvedValue(run({ id: 'run-9', status: 'cancelled' }));
		setUrl('?tab=presets&id=krea2');
		mounted = mount(PresetsTab);
		await settle();

		const startButton = Array.from(mounted.target.querySelectorAll('button')).find((button) =>
			button.textContent?.includes('Set up with recipe')
		)!;
		flushSync(() => startButton.click());
		await settle();

		const cancelButton = Array.from(
			mounted.target.querySelectorAll<HTMLButtonElement>('[data-recipe-run-conflict] button')
		).find((button) => button.textContent?.trim() === 'Cancel it')!;
		flushSync(() => cancelButton.click());
		await settle();

		expect(api.api.applyRecipeRunAction).toHaveBeenCalledWith('run-9', 'cancel');
		expect(mounted.target.querySelector('[data-recipe-run-conflict]')).toBeNull();
	});

	it('attaches to the active run instead of erroring when the same recipe is already running', async () => {
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [preset()] } as never);
		vi.mocked(api.api.getPreset).mockResolvedValue({ success: true, data: preset() } as never);
		vi.mocked(api.api.createRecipeRun).mockRejectedValue(
			conflictError({
				id: 'run-9',
				recipe_id: 'krea2-starter',
				recipe_name: 'Krea-2 Starter',
				status: 'running'
			})
		);
		vi.mocked(api.api.getRecipeRun).mockResolvedValue(run({ id: 'run-9', status: 'running' }));
		setUrl('?tab=presets&id=krea2');
		mounted = mount(PresetsTab);
		await settle();

		const startButton = Array.from(mounted.target.querySelectorAll('button')).find((button) =>
			button.textContent?.includes('Set up with recipe')
		)!;
		flushSync(() => startButton.click());
		await settle();

		expect(api.api.getRecipeRun).toHaveBeenCalledWith('run-9');
		expect(mounted.target.querySelector('[data-recipe-run-conflict]')).toBeNull();
		expect(mounted.target.textContent).toContain('Setting up');
		expect(mounted.target.textContent).not.toContain('409');
	});

	it('shows the same notice from Admin -> Recipes when a different recipe is already running', async () => {
		vi.mocked(api.api.listRecipes).mockResolvedValue({ recipes: [recipeSummary()] });
		vi.mocked(api.api.getRecipeReadiness).mockResolvedValue({ overall: 'ready', checks: [] });
		vi.mocked(api.api.getRecipe).mockResolvedValue({
			...recipeSummary(),
			steps: [],
			artifacts: [],
			smoke: null,
			load_errors: []
		});
		vi.mocked(api.api.listRecipeRuns).mockResolvedValue({ runs: [] });
		vi.mocked(api.api.createRecipeRun).mockRejectedValue(
			conflictError({
				id: 'run-9',
				recipe_id: 'krea2-starter',
				recipe_name: 'Krea-2 Starter',
				status: 'awaiting_consent'
			})
		);
		setUrl('?tab=recipes&id=minimax-h3-starter');
		mounted = mount(RecipesTab);
		await settle();

		const installButton = Array.from(mounted.target.querySelectorAll('button')).find(
			(button) => button.textContent?.trim() === 'Install models'
		) as HTMLButtonElement;
		flushSync(() => installButton.click());
		await settle();

		const notice = mounted.target.querySelector('[data-recipe-run-conflict]') as HTMLElement;
		expect(notice).not.toBeNull();
		expect(notice.textContent).toContain('Krea-2 Starter');
		expect(mounted.target.textContent).not.toContain('409');
	});
});
