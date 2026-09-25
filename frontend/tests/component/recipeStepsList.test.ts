import { describe, it, expect, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { RecipeStepView } from '$lib/services/api/recipes';

const { default: RecipeStepsList } = await import(
	'../../src/routes/admin/components/recipes/RecipeStepsList.svelte'
);

let target: HTMLDivElement;
let component: ReturnType<typeof mount> | null = null;

const steps: RecipeStepView[] = [
	{ key: 'plugins_ensure', kind: 'plugins.ensure', title: 'Enable the backend plugin', onboarding_only: false },
	{ key: 'backend_ensure', kind: 'backend.ensure', title: 'Configure the backend', onboarding_only: true },
	{ key: 'artifacts_fetch', kind: 'artifacts.fetch', title: 'Download the models', onboarding_only: false },
	{ key: 'preset_ensure', kind: 'preset.ensure', title: 'Install the preset', onboarding_only: false },
	{ key: 'generation_smoke', kind: 'generation.smoke', title: 'Run a test generation', onboarding_only: false }
];

function mountList(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(RecipeStepsList, {
		target,
		props: { steps, ...props }
	});
	flushSync();
}

afterEach(() => {
	if (component) {
		unmount(component);
		component = null;
	}
	target?.remove();
});

describe('RecipeStepsList', () => {
	it('lists every step with its title, in order', () => {
		mountList();
		const rows = document.querySelectorAll('[data-recipe-step]');
		expect(rows.length).toBe(steps.length);
		rows.forEach((row, index) => {
			expect(row.getAttribute('data-recipe-step')).toBe(steps[index].key);
			expect(row.textContent).toContain(steps[index].title);
		});
	});

	it('marks onboarding-only steps', () => {
		mountList();
		const backendStep = document.querySelector('[data-recipe-step="backend_ensure"]') as HTMLElement;
		expect(backendStep.textContent).toContain('first run only');
		const pluginStep = document.querySelector('[data-recipe-step="plugins_ensure"]') as HTMLElement;
		expect(pluginStep.textContent).not.toContain('first run only');
	});


});
