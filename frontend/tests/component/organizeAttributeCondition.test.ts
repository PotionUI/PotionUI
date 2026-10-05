import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { attributeOptionsFixture, modelCatalogFixture } from './organizeFixtures';

const getOrganizeFactOptions = vi.fn();

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return {
		...actual,
		api: {
			...actual.api,
			getOrganizeFactOptions: (...args: unknown[]) => getOrganizeFactOptions(...args)
		}
	};
});

const { default: Host } = await import('./stubs/OrganizeBuilderHost.svelte');
const { emptyDraft, nextUid } = await import('../../src/lib/organize/draft');

async function settle() {
	for (let i = 0; i < 6; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let instance: ReturnType<typeof mount> | null = null;
let target: HTMLElement;

function render(conditions: { fact: string; operator: string; value: unknown }[]) {
	const draft = { ...emptyDraft('model'), conditions: conditions.map((c) => ({ uid: nextUid(), ...c })) };
	instance = mount(Host, { target, props: { initial: draft, catalog: modelCatalogFixture } });
}

function json() {
	return JSON.parse(target.querySelector('[data-testid="draft-json"]')!.textContent ?? '[]');
}

function trigger(within: Element): HTMLElement {
	return within.querySelector('[aria-haspopup="listbox"]') as HTMLElement;
}

function openOptions(within: Element): HTMLElement[] {
	trigger(within).click();
	flushSync();
	return Array.from(document.body.querySelectorAll<HTMLElement>('[role="option"]'));
}

function optionLabels(within: Element): string[] {
	const labels = openOptions(within).map((o) => o.textContent?.trim() ?? '');
	trigger(within).click();
	flushSync();
	return labels;
}

function pickAttribute(label: string) {
	const picker = target.querySelector('[data-testid="attribute-picker"]')!;
	const row = openOptions(picker).find((o) => o.textContent?.includes(label));
	if (!row) throw new Error(`no attribute option ${label}`);
	row.click();
	flushSync();
}

beforeEach(() => {
	target = document.createElement('div');
	document.body.appendChild(target);
	getOrganizeFactOptions.mockReset();
	getOrganizeFactOptions.mockImplementation(async (key: string) =>
		key === 'attribute'
			? { success: true, data: attributeOptionsFixture }
			: { success: true, data: [{ value: 'lora', label: 'lora' }, { value: 'checkpoint', label: 'checkpoint' }] }
	);
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target.remove();
	document.body.innerHTML = '';
});

describe('attribute conditions', () => {
	it('renders the attribute picker with every attribute and its type label', async () => {
		render([{ fact: 'attribute', operator: 'is', value: { key: '', type: '', label: '', value: null } }]);
		await settle();
		expect(getOrganizeFactOptions).toHaveBeenCalledWith('attribute', { subject: 'model', limit: 200 });
		const picker = target.querySelector('[data-testid="attribute-picker"]')!;
		const labels = optionLabels(picker);
		expect(labels).toHaveLength(4);
		expect(labels[0]).toContain('Recommended strength');
		expect(labels[0]).toContain('Number range');
		expect(target.querySelector('[data-testid="attribute-operator"]')).toBeNull();
	});

	it('picking a number attribute shows number operators and a number input', async () => {
		render([{ fact: 'attribute', operator: 'is', value: { key: '', type: '', label: '', value: null } }]);
		await settle();
		pickAttribute('Recommended strength');
		await settle();
		expect(json()[0]).toEqual({
			fact: 'attribute',
			operator: 'is',
			value: { key: 'strength', type: 'number', label: 'Recommended strength', value: 0 }
		});
		const operators = optionLabels(target.querySelector('[data-testid="attribute-operator"]')!);
		expect(operators).toEqual(['is', 'is at least', 'is at most']);
		const input = target.querySelector('[data-kind="number"] input[type="number"]') as HTMLInputElement;
		expect(input).not.toBeNull();
		expect(input.getAttribute('step')).toBe('0.05');
		input.value = '0.8';
		input.dispatchEvent(new Event('input', { bubbles: true }));
		flushSync();
		expect(json()[0].value.value).toBe(0.8);
	});

	it('picking an enum attribute shows its choices and keeps a list for is any of', async () => {
		render([{ fact: 'attribute', operator: 'is_any_of', value: { key: 'style', type: 'enum', label: 'Style', value: [] } }]);
		await settle();
		const chips = [...target.querySelectorAll('[data-kind="enum"] button')] as HTMLButtonElement[];
		expect(chips.map((b) => b.textContent?.trim())).toEqual(['Anime', 'Photo']);
		chips[1].click();
		flushSync();
		expect(json()[0].value).toEqual({ key: 'style', type: 'enum', label: 'Style', value: ['photo'] });
	});

	it('picking a yes or no attribute shows a switch', async () => {
		render([{ fact: 'attribute', operator: 'is', value: { key: '', type: '', label: '', value: null } }]);
		await settle();
		pickAttribute('Safe to share');
		await settle();
		const toggle = target.querySelector('[data-kind="bool"] [role="switch"]');
		expect(toggle).not.toBeNull();
		expect(json()[0].value).toMatchObject({ key: 'shareable', type: 'bool', value: true });
		expect(optionLabels(target.querySelector('[data-testid="attribute-operator"]')!)).toEqual(['is']);
	});

	it('text attributes get the text operators and an input', async () => {
		render([{ fact: 'attribute', operator: 'contains', value: { key: 'triggers', type: 'text', label: 'Trigger words', value: 'pony' } }]);
		await settle();
		expect(optionLabels(target.querySelector('[data-testid="attribute-operator"]')!)).toEqual([
			'contains',
			'does not contain',
			'starts with',
			'ends with',
			'is',
			'is not'
		]);
		expect((target.querySelector('[data-kind="text"] input') as HTMLInputElement).value).toBe('pony');
	});

	it('narrows the attributes to the model types the rule already checks', async () => {
		render([
			{ fact: 'model_type', operator: 'is', value: 'lora' },
			{ fact: 'attribute', operator: 'is', value: { key: '', type: '', label: '', value: null } }
		]);
		await settle();
		const labels = optionLabels(target.querySelector('[data-testid="attribute-picker"]')!);
		expect(labels.map((l) => l.split(/Number|Tags/)[0].trim())).toEqual(['Recommended strength', 'Trigger words']);
	});

	it('keeps the picked attribute visible after the model type narrows it out', async () => {
		render([
			{ fact: 'model_type', operator: 'is_any_of', value: ['lora'] },
			{ fact: 'attribute', operator: 'is', value: { key: 'style', type: 'enum', label: 'Style', value: 'anime' } }
		]);
		await settle();
		const labels = optionLabels(target.querySelector('[data-testid="attribute-picker"]')!);
		expect(labels.some((l) => l.includes('Style'))).toBe(true);
		expect(labels.some((l) => l.includes('Safe to share'))).toBe(false);
	});

	it('changing the operator keeps the attribute and reshapes the value', async () => {
		render([{ fact: 'attribute', operator: 'is', value: { key: 'style', type: 'enum', label: 'Style', value: 'anime' } }]);
		await settle();
		const operatorSelect = target.querySelector('[data-testid="attribute-operator"]')!;
		const row = openOptions(operatorSelect).find((o) => o.textContent?.trim() === 'is any of')!;
		row.click();
		flushSync();
		expect(json()[0]).toEqual({
			fact: 'attribute',
			operator: 'is_any_of',
			value: { key: 'style', type: 'enum', label: 'Style', value: ['anime'] }
		});
	});
});
