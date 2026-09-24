import { describe, it, expect, vi, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import type { RecipeStepView } from '$lib/services/api/recipes';

const { default: RecipeStepsModal } = await import(
	'../../src/routes/admin/components/recipes/RecipeStepsModal.svelte'
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

function mountModal(props: Record<string, unknown> = {}) {
	target = document.createElement('div');
	document.body.appendChild(target);
	component = mount(RecipeStepsModal, {
		target,
		props: { isOpen: true, steps, onClose: vi.fn(), ...props }
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

describe('RecipeStepsModal', () => {
	it('lists every step with its title, in order', () => {
		mountModal();
		const rows = document.querySelectorAll('[data-recipe-step]');
		expect(rows.length).toBe(steps.length);
		rows.forEach((row, index) => {
			expect(row.getAttribute('data-recipe-step')).toBe(steps[index].key);
			expect(row.textContent).toContain(steps[index].title);
		});
	});

	it('marks onboarding-only steps', () => {
		mountModal();
		const backendStep = document.querySelector('[data-recipe-step="backend_ensure"]') as HTMLElement;
		expect(backendStep.textContent).toContain('first run only');
		const pluginStep = document.querySelector('[data-recipe-step="plugins_ensure"]') as HTMLElement;
		expect(pluginStep.textContent).not.toContain('first run only');
	});

	it('highlights the current step of an active run', () => {
		mountModal({ currentStepKey: 'artifacts_fetch' });
		const currentStep = document.querySelector('[data-recipe-step="artifacts_fetch"]') as HTMLElement;
		expect(currentStep.getAttribute('data-current')).toBe('true');
		expect(currentStep.textContent).toContain('current');
		const otherStep = document.querySelector('[data-recipe-step="preset_ensure"]') as HTMLElement;
		expect(otherStep.getAttribute('data-current')).toBe('false');
	});

	it('renders nothing when closed', () => {
		mountModal({ isOpen: false });
		expect(document.querySelector('[data-recipe-step]')).toBeNull();
	});
});
