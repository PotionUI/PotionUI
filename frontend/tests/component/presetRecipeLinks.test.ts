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
		id: 'krea2-starter',
		name: 'Krea-2 Starter',
		summary: 'Get Krea-2 running.',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 3,
		total_download_bytes: 18_600_000_000,
		last_completed_at: null,
		source: 'marketplace',
		plugin_id: null,
		step_count: 4,
		presets: [{ id: 'krea2', name: 'Krea-2 Turbo', cover_url: null, installed: false }]
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

describe('Admin presets link to their recipes', () => {
	it('marks a preset card that has a recipe and names it in a tooltip', async () => {
		vi.mocked(api.api.listPresets).mockResolvedValue({
			success: true,
			data: [preset(), preset({ id: 'plain', name: 'Plain', recipes: [] })]
		} as never);
		setUrl('?tab=presets');
		mounted = mount(PresetsTab);
		await settle();

		const cards = Array.from(mounted.target.querySelectorAll('[data-preset-card]'));
		const kreaCard = cards.find((card) => card.getAttribute('aria-label') === 'Krea-2')!;
		const plainCard = cards.find((card) => card.getAttribute('aria-label') === 'Plain')!;
		const badge = kreaCard.querySelector('[data-preset-recipe-badge]') as HTMLElement;
		expect(badge?.textContent?.trim()).toBe('Recipe');
		expect(plainCard.querySelector('[data-preset-recipe-badge]')).toBeNull();

		badge.parentElement!.dispatchEvent(new MouseEvent('mouseenter'));
		await new Promise((resolve) => setTimeout(resolve, 250));
		expect(document.body.textContent).toContain('Krea-2 Starter');
	});

	it('starts the recipe run from the preset detail and shows its progress', async () => {
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [preset()] } as never);
		vi.mocked(api.api.getPreset).mockResolvedValue({ success: true, data: preset() } as never);
		vi.mocked(api.api.createRecipeRun).mockResolvedValue(run({ status: 'completed' }));
		setUrl('?tab=presets&id=krea2');
		mounted = mount(PresetsTab);
		await settle();

		const buttons = Array.from(mounted.target.querySelectorAll('button')).filter((button) =>
			button.textContent?.includes('Set up with recipe')
		);
		expect(buttons.length).toBeGreaterThan(0);
		expect(buttons[0].textContent).toContain('GB');

		flushSync(() => buttons[0].click());
		await settle();

		expect(api.api.createRecipeRun).toHaveBeenCalledWith('krea2-starter');
		expect(mounted.target.textContent).toContain('Setting up');
	});

	it('offers no recipe action for a preset without one', async () => {
		const plain = preset({ recipes: [] });
		vi.mocked(api.api.listPresets).mockResolvedValue({ success: true, data: [plain] } as never);
		vi.mocked(api.api.getPreset).mockResolvedValue({ success: true, data: plain } as never);
		setUrl('?tab=presets&id=krea2');
		mounted = mount(PresetsTab);
		await settle();

		expect(mounted.target.textContent).not.toContain('Set up with recipe');
		expect(mounted.target.querySelector('[data-preset-recipe-setup]')).toBeNull();
	});
});

describe('Admin recipes link to the presets they set up', () => {
	function setupRecipesApi() {
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
	}

	it('shows "Sets up:" with the preset name linking to its detail on the card', async () => {
		setupRecipesApi();
		mounted = mount(RecipesTab);
		await settle();

		const setsUp = mounted.target.querySelector('[data-recipe-sets-up]') as HTMLElement;
		expect(setsUp.textContent).toContain('Sets up:');
		const link = setsUp.querySelector('a') as HTMLAnchorElement;
		expect(link.textContent?.trim()).toBe('Krea-2 Turbo');
		expect(link.getAttribute('href')).toBe('/admin?tab=presets&id=krea2');
	});

	it('links the preset from the recipe detail', async () => {
		setupRecipesApi();
		setUrl('?tab=recipes&id=krea2-starter');
		mounted = mount(RecipesTab);
		await settle();

		const setsUp = mounted.target.querySelector('[data-recipe-sets-up]') as HTMLElement;
		const link = setsUp.querySelector('a') as HTMLAnchorElement;
		expect(link.textContent).toContain('Krea-2 Turbo');
		expect(link.getAttribute('href')).toBe('/admin?tab=presets&id=krea2');
	});
});
