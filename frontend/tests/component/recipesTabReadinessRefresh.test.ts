// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';
import { flushSync } from 'svelte';
import type { Writable } from 'svelte/store';
import type { RecipeSummary } from '$lib/services/api/recipes';
import type { ReadinessReport, SetupRun } from '$lib/services/api/setup';

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

function readinessReport(overrides: { status?: 'ready' | 'not_ready'; code?: string } = {}): ReadinessReport {
	const status = overrides.status ?? 'not_ready';
	return {
		overall: status,
		checks: [{ area: 'content', status, code: overrides.code ?? 'RECIPE_MODELS_MISSING', message: '', action: null }]
	};
}

function completedRun(overrides: Partial<SetupRun> = {}): SetupRun {
	return {
		id: 'run-1',
		recipe_id: 'krea2-starter',
		recipe_version: 1,
		scope: 'instance',
		mode: 'admin',
		status: 'completed',
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

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
	vi.clearAllMocks();
	page.update((current) => ({ ...current, url: new URL('http://localhost/admin') }));
	libraryCardDensity.set('compact');
});

describe('RecipesTab readiness refresh after a run finishes', () => {
	it('flips the badge from Missing models to Ready once the run completes, without a reload', async () => {
		const recipes: RecipeSummary[] = [recipe()];
		(api.api.listRecipes as any).mockResolvedValue({ recipes });
		(api.api.getRecipeReadiness as any)
			.mockResolvedValueOnce(readinessReport({ status: 'not_ready' }))
			.mockResolvedValue(readinessReport({ status: 'ready', code: 'RECIPE_READY' }));
		(api.api.getRecipe as any).mockResolvedValue({
			...recipes[0],
			steps: [],
			artifacts: [],
			presets: [],
			smoke: null,
			load_errors: []
		});
		(api.api.listRecipeRuns as any).mockResolvedValue({ runs: [] });
		(api.api.createRecipeRun as any).mockResolvedValue(completedRun());

		mounted = mount();
		await settle();

		const grid = mounted.target.querySelector('[role="list"][aria-label="Recipe catalog"]') as HTMLElement;
		expect(grid.textContent).toContain('Missing models');

		const card = grid.querySelector('[data-library-card]') as HTMLElement;
		flushSync(() => card.click());
		await settle();

		expect(mounted.target.textContent).toContain('Missing models');

		const installButton = mounted.target.querySelector(
			'button[aria-label="Install models"]'
		) as HTMLButtonElement;
		expect(installButton, 'expected an Install models action').toBeTruthy();

		flushSync(() => installButton.click());
		await settle();

		expect(api.api.createRecipeRun).toHaveBeenCalledWith('krea2-starter');
		expect(api.api.getRecipeReadiness).toHaveBeenCalledTimes(2);

		const badge = Array.from(mounted.target.querySelectorAll('span')).find(
			(el) => el.textContent?.trim() === 'Ready'
		);
		expect(badge, 'expected the readiness badge to flip to Ready').toBeTruthy();
		expect(mounted.target.textContent).not.toContain('Missing models');
	});
});
