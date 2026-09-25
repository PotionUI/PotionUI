import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { RecipeRun } from '$lib/services/api/recipes';

const { default: RecipeRunsList } = await import(
	'../../src/routes/admin/components/recipes/RecipeRunsList.svelte'
);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

function run(overrides: Partial<RecipeRun> = {}): RecipeRun {
	return {
		id: `run-${Math.random()}`,
		recipe_id: 'krea2-starter',
		recipe_version: 1,
		scope: 'admin',
		mode: 'admin',
		status: 'succeeded',
		current_step: null,
		safe_input: null,
		safe_output: null,
		error_code: null,
		safe_error_detail: null,
		created_at: '2026-09-20T10:00:00Z',
		updated_at: '2026-09-20T10:05:00Z',
		completed_at: '2026-09-20T10:05:00Z',
		steps: [],
		attempts: [],
		...overrides
	} as RecipeRun;
}

function mountList(runs: RecipeRun[], limit?: number) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(RecipeRunsList, { target, props: { runs, ...(limit ? { limit } : {}) } });
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('RecipeRunsList', () => {
	it('shows every run when the history is short', () => {
		mountList([run(), run(), run()], 5);
		expect(target.querySelectorAll('[data-recipe-runs] .dt-row:not(.dt-row--head)').length).toBe(3);
		expect(target.querySelector('button')).toBeNull();
	});

	it('collapses to the limit and offers Show all when the history is long', () => {
		mountList(Array.from({ length: 8 }, () => run()), 5);
		expect(target.querySelectorAll('[data-recipe-runs] .dt-row:not(.dt-row--head)').length).toBe(5);
		const button = target.querySelector('button') as HTMLButtonElement;
		expect(button.textContent?.trim()).toBe('Show all 8 runs');
	});

	it('expands to the full history on Show all', () => {
		mountList(Array.from({ length: 8 }, () => run()), 5);
		(target.querySelector('button') as HTMLButtonElement).click();
		flushSync();
		expect(target.querySelectorAll('[data-recipe-runs] .dt-row:not(.dt-row--head)').length).toBe(8);
		expect(target.querySelector('button')).toBeNull();
	});

	it('renders an empty message when there are no runs', () => {
		mountList([]);
		expect(target.textContent).toContain("hasn't been run yet");
	});
});
