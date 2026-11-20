// @vitest-environment jsdom
import { describe, it, expect, vi, afterEach } from 'vitest';

vi.mock('$lib/stores/formulas', async () => {
	const { writable } = await import('svelte/store');
	return {
		formulaLists: writable({}),
		formulasKey: (preset: string, mode: string) => `${preset} ${mode}`,
		getFormulaDeclaration: vi.fn().mockResolvedValue({ groups: [{ id: 'look', label: 'Look', fields: ['a'] }] }),
		loadFormulas: vi.fn().mockResolvedValue(undefined)
	};
});

vi.mock('$lib/components/formulas/FormulasDrawer.svelte', async () => ({
	default: (await import('./fixtures/FormulasDrawerStub.svelte')).default
}));

const { default: Fixture } = await import('./fixtures/FormulasButtonSiblingsFixture.svelte');
const { mountComponent } = await import('./helpers/mount');
const { settle } = await import('./helpers/settle');

afterEach(() => {
	document.body.innerHTML = '';
});

describe('closing the formulas drawer leaves the controls after the button in place', () => {
	for (const layout of ['dock', 'sheet', 'rail'] as const) {
		it(`${layout} layout`, async () => {
			mountComponent(Fixture, { layout });
			await settle();
			const button = document.querySelector<HTMLButtonElement>('[data-testid="formulas-button"]')!;
			button.click();
			await settle();
			const apply = document.querySelector<HTMLButtonElement>('[data-stub-apply]');
			expect(apply).not.toBeNull();
			apply!.click();
			await settle();
			expect(document.querySelector('[data-stub-apply]')).toBeNull();
			expect(document.querySelector('[data-after-changed]')).not.toBeNull();
			expect(document.querySelector('[data-after-settings]')).not.toBeNull();
		});
	}
});
