// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { RecipeSummary } from '$lib/services/api/recipes';
import type { ReadinessReport } from '$lib/services/api/setup';

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
			getRecipeRun: vi.fn()
		}
	};
});

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
const { default: RecipesTab } = await import('../../src/routes/admin/components/RecipesTab.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { libraryCardDensity } = await import('$lib/components/library/libraryCardDensity');

function recipe(overrides: Partial<RecipeSummary> = {}): RecipeSummary {
	return {
		id: 'krea2-starter',
		name: 'Krea-2 Starter',
		summary: 'Fetches Krea-2 Turbo and proves a first text-to-image generation works end to end.',
		description: '',
		engine: 'native',
		category: 'image',
		artifact_count: 3,
		total_download_bytes: 18_600_000_000,
		last_completed_at: null,
		source: 'marketplace',
		plugin_id: null,
		step_count: 4,
		presets: [{ id: 'krea-2/turbo', name: 'Krea-2 Turbo', cover_url: null, installed: false }],
		...overrides
	};
}

function readinessReport(): ReadinessReport {
	return {
		overall: 'ready',
		checks: [{ area: 'content', status: 'ready', code: 'ok', message: '', action: null }]
	};
}

const RECIPES: RecipeSummary[] = [
	recipe(),
	recipe({
		id: 'sdxl-starter',
		name: 'SDXL Starter',
		summary: 'Installs SDXL base + refiner and runs a first txt2img smoke test.',
		category: 'image',
		artifact_count: 2,
		total_download_bytes: 6_900_000_000,
		last_completed_at: '2026-09-01T00:00:00Z'
	})
];

function setupApi() {
	(api.api.listRecipes as any).mockResolvedValue({ recipes: RECIPES });
	(api.api.getRecipeReadiness as any).mockImplementation(async () => readinessReport());
	(api.api.getRecipe as any).mockResolvedValue({ ...RECIPES[0], steps: [], artifacts: [], presets: [], smoke: null, load_errors: [] });
	(api.api.listRecipeRuns as any).mockResolvedValue({ runs: [] });
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

async function settle() {
	for (let i = 0; i < 8; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

function catalogGrid(target: HTMLElement): HTMLElement {
	const grid = target.querySelector('[role="list"][aria-label="Recipe catalog"]') as HTMLElement | null;
	expect(grid, 'expected a card grid for the recipe catalog').toBeTruthy();
	return grid!;
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	page.update((current) => ({ ...current, url: new URL('http://localhost/admin') }));
	libraryCardDensity.set('compact');
});

describe('RecipesTab card grid', () => {
	it('shows name, category, readiness and the artifact/size summary on each card', async () => {
		setupApi();
		mounted = mount();
		await settle();

		const grid = catalogGrid(mounted.target);
		expect(grid.textContent).toContain('Krea-2 Starter');
		expect(grid.textContent).toContain('Image');
		expect(grid.textContent).toContain('Ready');
		expect(grid.textContent).toContain('3 artifacts');
		expect(grid.textContent).toContain('GB');
		expect(grid.textContent).toContain('installed');
	});

	it('renders cards when the list payload carries no presets field', async () => {
		setupApi();
		const { presets: _omit, ...withoutPresets } = recipe();
		(api.api.listRecipes as any).mockResolvedValue({ recipes: [withoutPresets] });
		mounted = mount();
		await settle();

		const grid = catalogGrid(mounted.target);
		expect(grid.textContent).toContain('Krea-2 Starter');
		expect(grid.querySelector('[data-recipe-sets-up]')).toBeNull();
	});

	it('opens the recipe detail view when a card is activated', async () => {
		setupApi();
		mounted = mount();
		await settle();

		const grid = catalogGrid(mounted.target);
		const card = Array.from(grid.querySelectorAll('[data-library-card]')).find((el) =>
			el.textContent?.includes('SDXL Starter')
		) as HTMLElement | undefined;
		expect(card, 'expected a card for SDXL Starter').toBeTruthy();

		flushSync(() => card!.click());
		await settle();

		expect(mounted.target.querySelector('[role="list"][aria-label="Recipe catalog"]')).toBeFalsy();
		expect(mounted.target.textContent).toContain('SDXL Starter');
	});

	it('the density toggle hides descriptions and tightens the grid', async () => {
		setupApi();
		mounted = mount();
		await settle();

		const grid = catalogGrid(mounted.target);
		expect(grid.className).toContain('minmax(300px');
		expect(grid.textContent).toContain('Fetches Krea-2 Turbo');

		const denseButton = Array.from(mounted.target.querySelectorAll('button[role="radio"]')).find(
			(el) => el.textContent?.trim() === 'Dense'
		) as HTMLButtonElement | undefined;
		expect(denseButton, 'expected a Dense option in the density toggle').toBeTruthy();
		flushSync(() => denseButton!.click());
		await settle();

		const denseGrid = catalogGrid(mounted.target);
		expect(denseGrid.className).toContain('minmax(240px');
		expect(denseGrid.textContent).not.toContain('Fetches Krea-2 Turbo');
	});
});
