// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { mount, unmount, flushSync } from 'svelte';
import { catalogFixture } from './organizeFixtures';

const getOrganizeFactOptions = vi.fn();
const getModelById = vi.fn();

vi.mock('$lib/services/api', async () => {
	const actual = await vi.importActual<typeof import('$lib/services/api')>('$lib/services/api');
	return {
		...actual,
		api: {
			...actual.api,
			getOrganizeFactOptions: (...args: unknown[]) => getOrganizeFactOptions(...args),
			getModelById: (...args: unknown[]) => getModelById(...args),
			getModels: vi.fn(async () => ({ success: true, data: { models: [], total: 0 } })),
			getModelTypes: vi.fn(async () => ({ success: true, data: { types: [] } }))
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
	const draft = { ...emptyDraft('generation'), conditions: conditions.map((c) => ({ uid: nextUid(), ...c })) };
	instance = mount(Host, { target, props: { initial: draft, catalog: catalogFixture } });
}

function json() {
	return JSON.parse(target.querySelector('[data-testid="draft-json"]')!.textContent ?? '[]');
}

beforeEach(() => {
	target = document.createElement('div');
	document.body.appendChild(target);
	getOrganizeFactOptions.mockReset();
	getOrganizeFactOptions.mockResolvedValue({ success: true, data: [{ value: 'landscape', label: 'landscape' }] });
	getModelById.mockReset();
	getModelById.mockResolvedValue({ success: true, data: { model: { id: 'm1', name: 'Krea-2 Turbo' } } });
});

afterEach(() => {
	if (instance) unmount(instance);
	instance = null;
	target.remove();
});

describe('ConditionBuilder driven by the catalog', () => {
	it('renders a control per fact kind', async () => {
		render([
			{ fact: 'model', operator: 'is', value: 'm1' },
			{ fact: 'media_kind', operator: 'is', value: 'video' },
			{ fact: 'preset', operator: 'is', value: '' },
			{ fact: 'resolution', operator: 'is', value: { width: 1344, height: 768 } },
			{ fact: 'prompt', operator: 'contains', value: 'cat' },
			{ fact: 'tags', operator: 'has', value: ['sunset'] }
		]);
		await settle();
		const kinds = [...target.querySelectorAll('[data-kind]')].map((el) => el.getAttribute('data-kind'));
		expect(kinds).toEqual(['model_ref', 'enum', 'enum', 'size', 'text', 'tag_list']);
		expect(target.textContent).toContain('Krea-2 Turbo');
		expect(target.querySelector('[data-fact="media_kind"] [role="group"]')).not.toBeNull();
		expect(target.querySelector('[data-fact="preset"] [role="group"]')).toBeNull();
		expect(target.querySelector('[data-testid="tag-names-input"]')).not.toBeNull();
		expect(target.querySelector('input[aria-label="Prompt"]')).not.toBeNull();
	});

	it('shows the size presets with the matching one selected, and custom fields for an odd size', async () => {
		render([{ fact: 'resolution', operator: 'is', value: { width: 1344, height: 768 } }]);
		await settle();
		expect(target.querySelector('input[aria-label="Width"]')).toBeNull();
		unmount(instance!);
		target.innerHTML = '';
		render([{ fact: 'resolution', operator: 'is', value: { width: 999, height: 555 } }]);
		await settle();
		expect((target.querySelector('input[aria-label="Width"]') as HTMLInputElement).value).toBe('999');
	});

	it('falls back to a text input for a plugin fact with an unknown kind', async () => {
		render([{ fact: 'tagger.mood', operator: 'is', value: 'calm' }]);
		await settle();
		const control = target.querySelector('[data-fact="tagger.mood"]')!;
		expect(control.getAttribute('data-kind')).toBe('text');
		expect((control.querySelector('input') as HTMLInputElement).value).toBe('calm');
	});

	it('loads tag suggestions from the options endpoint for tag lists', async () => {
		render([{ fact: 'tags', operator: 'has', value: [] }]);
		await settle();
		const input = target.querySelector('[data-testid="tag-names-input"] input') as HTMLInputElement;
		input.focus();
		input.dispatchEvent(new FocusEvent('focus'));
		await new Promise((resolve) => setTimeout(resolve, 300));
		flushSync();
		expect(getOrganizeFactOptions).toHaveBeenCalledWith('tags', { subject: 'generation', q: '', limit: 20 });
	});

	it('toggles chips into a list and keeps the draft in sync', async () => {
		render([{ fact: 'media_kind', operator: 'is_any_of', value: [] }]);
		await settle();
		const chips = [...target.querySelectorAll('[data-fact="media_kind"] button')] as HTMLButtonElement[];
		chips.find((b) => b.textContent?.trim() === 'Video')!.click();
		flushSync();
		chips.find((b) => b.textContent?.trim() === 'Image')!.click();
		flushSync();
		expect(json()[0].value).toEqual(['video', 'image']);
	});

	it('adds and removes conditions', async () => {
		render([]);
		await settle();
		expect(target.querySelector('[data-testid="no-conditions"]')).not.toBeNull();
		const add = [...target.querySelectorAll('button')].find((b) => b.textContent?.includes('Add condition'))!;
		add.click();
		flushSync();
		expect(json()).toHaveLength(1);
		expect(json()[0].fact).toBe('model');
		(target.querySelector('button[aria-label="Remove condition"]') as HTMLButtonElement).click();
		flushSync();
		expect(json()).toHaveLength(0);
	});
});
