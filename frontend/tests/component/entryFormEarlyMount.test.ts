// @vitest-environment jsdom
import { afterEach, describe, expect, it, vi } from 'vitest';
import { mountComponent } from './helpers/mount';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({ api: {} }));

const { default: EntryForm } = await import('$lib/components/tool-tabs/frame/EntryForm.svelte');
const { toolParamsToSchema } = await import('$lib/media-tools/toolSchema');

afterEach(() => {
	document.body.innerHTML = '';
});

describe('EntryForm on its own', () => {
	it('draws real controls for every editor field type with nothing else registered and no api behind it', async () => {
		const fields = toolParamsToSchema([
			{ name: 'a', type: 'float', min: 0, max: 1, default: 0.5 },
			{ name: 'b', type: 'integer' },
			{ name: 'c', type: 'integer' },
			{ name: 'seed', type: 'integer', min: 0, max: 99 },
			{ name: 'd', type: 'select', options: ['x', 'y'] },
			{ name: 'e', type: 'select', options: ['1', '2', '3', '4', '5'] },
			{ name: 'f', type: 'checkbox' },
			{ name: 'g', type: 'color' },
			{ name: 'h', type: 'anchor' },
			{ name: 'i', type: 'string' }
		] as never);
		mountComponent(EntryForm as never, { fields, values: {}, onValues: () => undefined });
		await settle(12);
		expect(document.body.textContent).not.toContain('Unsupported field type');
		const types = Array.from(document.body.querySelectorAll<HTMLElement>('[data-field-type]')).map((node) => node.dataset.fieldType);
		for (const type of ['slider', 'number_box', 'seed', 'segmented', 'select', 'checkbox', 'color', 'anchor', 'string', 'row']) expect(types).toContain(type);
		expect(document.body.querySelectorAll('input, button').length).toBeGreaterThan(10);
	});
});
