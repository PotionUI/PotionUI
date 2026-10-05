// @vitest-environment jsdom
import { describe, it, expect, afterEach, beforeEach } from 'vitest';
import { get } from 'svelte/store';

const { default: FormField } = await import('../../src/lib/components/form-fields/FormField.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { flushSync } = await import('svelte');
const { tabsStore } = await import('$lib/stores/tabs');
const store = await import('$lib/generation/compare/compareStore.svelte');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');
const { ACTIVE_TAB_ID_CONTEXT_KEY } = await import('$lib/form/activeTabContext');

registerBuiltinFieldComponents();

const sampler = {
	field: 'sampler',
	type: 'select',
	label: 'Sampler',
	values: ['euler', 'er_sde', 'heun', 'uni_pc'].map((value) => ({ value, label: value }))
};

const samplerConfig = {
	type: 'select',
	title: 'Sampler',
	options: sampler.values.map((v) => ({ value: v.value, label: v.label }))
};

let tabId = '';
let mounted: { target: HTMLElement; destroy: () => void } | undefined;

function mountField(name: string, config: Record<string, unknown>, value: unknown) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: FormField as never,
		target,
		props: { name, config, value, onChange: () => {} },
		context: new Map([[ACTIVE_TAB_ID_CONTEXT_KEY, tabId]])
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

async function settle() {
	for (let i = 0; i < 5; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

beforeEach(() => {
	tabsStore.reset();
	store.resetCompareStoreForTests();
	tabId = get(tabsStore).tabs[0].id;
});

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('axis fields in the form', () => {
	it('renders the normal control while Compare is off', async () => {
		mounted = mountField('sampler', samplerConfig, 'euler');
		await settle();
		expect(mounted.target.querySelector('[data-axis-locked]')).toBeNull();
		expect(mounted.target.textContent).toContain('Sampler');
	});

	it('locks an axis field with the X AXIS tag, value count and form value, opening the drawer on click', async () => {
		store.setCompare(tabId, { armed: true, x: sampler });
		mounted = mountField('sampler', samplerConfig, 'euler');
		await settle();

		const locked = mounted.target.querySelector<HTMLButtonElement>('[data-axis-locked="x"]')!;
		expect(locked).toBeTruthy();
		expect(locked.textContent).toContain('x axis');
		expect(locked.textContent).toContain('4 values');
		expect(locked.textContent).toContain('euler');
		expect(mounted.target.querySelector('select, input')).toBeNull();

		expect(store.isCompareDrawerOpen(tabId)).toBe(false);
		locked.click();
		expect(store.isCompareDrawerOpen(tabId)).toBe(true);
	});

	it('tags the Y axis and unlocks the field again when Compare is turned off', async () => {
		store.setCompare(tabId, { armed: true, y: { ...sampler, values: sampler.values.slice(0, 3) } });
		mounted = mountField('sampler', samplerConfig, 'euler');
		await settle();
		const locked = mounted.target.querySelector('[data-axis-locked="y"]');
		expect(locked?.textContent).toContain('3 values');

		store.turnOffCompare(tabId);
		await settle();
		expect(mounted.target.querySelector('[data-axis-locked]')).toBeNull();
	});

	it('leaves fields that are not on an axis editable', async () => {
		store.setCompare(tabId, { armed: true, x: sampler });
		mounted = mountField('scheduler', { type: 'select', title: 'Scheduler', options: [{ value: 'beta', label: 'beta' }] }, 'beta');
		await settle();
		expect(mounted.target.querySelector('[data-axis-locked]')).toBeNull();
	});

	it('notes that quantity is 1 per cell only while Compare has cells', async () => {
		store.publishCompareSchema(tabId, 'p-txt2img', { properties: {}, quantity_fields: ['quantity'] });
		const quantity = { type: 'stepper', title: 'Quantity', minimum: 1, maximum: 10 };
		mounted = mountField('quantity', quantity, 3);
		await settle();
		expect(mounted.target.querySelector('[data-compare-quantity-note]')).toBeNull();
		store.setCompare(tabId, { armed: true, x: sampler });
		await settle();
		expect(mounted.target.querySelector('[data-compare-quantity-note]')?.textContent).toContain('1 per cell');
	});

	it('puts the quantity note on whichever field the preset reads as quantity', async () => {
		store.publishCompareSchema(tabId, 'cloud-txt2img', { properties: {}, quantity_fields: ['count'] });
		store.setCompare(tabId, { armed: true, x: sampler });
		const stepper = { type: 'stepper', title: 'Quantity', minimum: 1, maximum: 8 };
		mounted = mountField('count', stepper, 3);
		const named = mountField('quantity', stepper, 3);
		await settle();
		expect(mounted.target.querySelector('[data-compare-quantity-note]')?.textContent).toContain('1 per cell');
		expect(named.target.querySelector('[data-compare-quantity-note]')).toBeNull();
		named.destroy();
	});
});
