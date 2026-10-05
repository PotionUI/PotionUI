import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));

const { api } = await import('$lib/services/api/index');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: TextInput } = await import('$lib/components/form-fields/TextInput.svelte');
const { default: CheckboxField } = await import('$lib/components/form-fields/CheckboxField.svelte');
const { default: TabsField } = await import('$lib/components/form-fields/TabsField.svelte');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { createClassComponent } = await import('svelte/legacy');

registerFieldComponent('string', { component: TextInput });
registerFieldComponent('boolean', { component: CheckboxField });
registerFieldComponent('tabs', { component: TabsField });

function reaction(field: string, operator: string, value: unknown, then: Record<string, unknown>) {
	return {
		when: { field, operator, value, [operator]: value },
		then: {
			set_visibility: null, set_value: null, set_disabled: null, update_options: null,
			update_validation: null, set_filter_tags: null, ...then
		}
	};
}

const mainTab = {
	type: 'tab',
	name: 'main_tab',
	label: 'Main',
	children: [
		{ name: 'prompt', type: 'string', title: 'Prompt', default: 'p0' },
		{ name: 'extras_on', type: 'boolean', title: 'Extras', default: false }
	]
};

const extrasTab = (extra: Record<string, unknown> = {}) => ({
	type: 'tab',
	name: 'extras_tab',
	label: 'Extras',
	children: [{ name: 'strength', type: 'string', title: 'Strength', default: 's0' }],
	...extra
});

const reactiveExtrasTab = extrasTab({
	reactions: [
		reaction('extras_on', 'equals', true, { set_visibility: true }),
		reaction('extras_on', 'not_equals', true, { set_visibility: false })
	]
});

const loraTab = {
	type: 'tab',
	name: 'lora_tab',
	label: 'Loras',
	children: [{ name: 'lora_note', type: 'string', title: 'Note', default: 'n0' }]
};

function schema(tabs: unknown[]) {
	return { properties: { layout: { type: 'tabs', children: tabs } } };
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let destroy: (() => void) | undefined;

function mountForm(tabs: unknown[], initialData: Record<string, unknown> = {}) {
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({
		success: true,
		data: { preset_id: 'p', form_schema: schema(tabs) }
	} as never);
	const target = document.createElement('div');
	document.body.appendChild(target);
	const log: Array<Record<string, unknown>> = [];
	const form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: {
			presetId: `tabs-${Math.random()}`,
			mode: 'txt2img',
			initialData,
			onFormDataChange: (data: Record<string, unknown>) => log.push(data)
		}
	});
	destroy = () => {
		form.$destroy();
		target.remove();
	};
	const tabButtons = () => Array.from(target.querySelectorAll<HTMLButtonElement>('[role="tab"]'));
	const input = (name: string) => target.querySelector<HTMLInputElement>(`[data-field-name="${name}"] input`);
	const panelOf = (name: string) => input(name)!.closest('[role="tabpanel"]') as HTMLElement;
	return {
		target,
		form,
		log,
		tabButtons,
		input,
		panelOf,
		lastData: () => log[log.length - 1],
		toggleExtras: async () => {
			input('extras_on')!.click();
			await settle();
		},
		clickTab: async (label: string) => {
			tabButtons().find((b) => b.getAttribute('aria-label') === label)!.click();
			await settle();
		}
	};
}

beforeEach(() => {
	clearSchemaCache();
});

afterEach(() => {
	destroy?.();
	destroy = undefined;
});

describe('Tab bar with a single visible tab', () => {
	it('renders the content without a tab bar when the preset has one tab', async () => {
		const m = mountForm([mainTab]);
		await settle();
		expect(m.tabButtons()).toHaveLength(0);
		expect(m.target.querySelector('[role="tablist"]')).toBeNull();
		expect(m.input('prompt')).toBeTruthy();
		expect(m.panelOf('prompt').classList.contains('hidden')).toBe(false);
		expect(m.panelOf('prompt').className).toContain('pt-3');
	});

	it('renders the bar when two tabs are visible', async () => {
		const m = mountForm([mainTab, extrasTab()]);
		await settle();
		expect(m.tabButtons().map((b) => b.getAttribute('aria-label'))).toEqual(['Main', 'Extras']);
		expect(m.panelOf('prompt').className).toContain('pt-3');
	});

	it('hides the bar when a second tab is hidden by an admin-style visible=false', async () => {
		const m = mountForm([mainTab, extrasTab({ visible: false })]);
		await settle();
		expect(m.tabButtons()).toHaveLength(0);
		expect(m.input('prompt')).toBeTruthy();
	});

	it('shows and hides the bar live as a reaction toggles the second tab, keeping values and the active tab', async () => {
		const m = mountForm([mainTab, reactiveExtrasTab]);
		await settle();
		expect(m.tabButtons()).toHaveLength(0);

		m.input('prompt')!.value = 'typed';
		m.input('prompt')!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		await m.toggleExtras();
		expect(m.tabButtons()).toHaveLength(2);
		expect(m.tabButtons()[0].getAttribute('aria-selected')).toBe('true');
		expect(m.input('prompt')!.value).toBe('typed');
		expect(m.lastData().prompt).toBe('typed');

		await m.toggleExtras();
		expect(m.tabButtons()).toHaveLength(0);
		expect(m.input('prompt')!.value).toBe('typed');
		expect(m.lastData().prompt).toBe('typed');
	});

	it('falls back to the first visible tab when the active tab gets hidden, without losing values', async () => {
		const hideable = {
			...mainTab,
			reactions: [
				reaction('extras_on', 'equals', true, { set_visibility: false }),
				reaction('extras_on', 'not_equals', true, { set_visibility: true })
			]
		};
		const m = mountForm([hideable, extrasTab()]);
		await settle();
		expect(m.tabButtons()).toHaveLength(2);
		m.input('strength')!.value = 'kept';
		m.input('strength')!.dispatchEvent(new Event('input', { bubbles: true }));
		await settle();

		await m.toggleExtras();
		expect(m.tabButtons()).toHaveLength(0);
		expect(m.panelOf('strength').classList.contains('hidden')).toBe(false);
		expect(m.input('strength')!.value).toBe('kept');
		expect(m.lastData().strength).toBe('kept');
		expect(m.lastData().prompt).toBe('p0');
	});
});

describe('Session round trip across tab layouts', () => {
	it('loads values saved under a one-tab preset into a preset that now has three tabs', async () => {
		const m = mountForm([mainTab, extrasTab(), loraTab], { prompt: 'saved', extras_on: false });
		await settle();
		expect(m.tabButtons()).toHaveLength(3);
		expect(m.input('prompt')!.value).toBe('saved');
		expect(m.input('strength')!.value).toBe('s0');
		expect(m.input('lora_note')!.value).toBe('n0');
		expect(m.lastData().prompt).toBe('saved');
	});

	it('loads values saved on a non-first tab into a preset where that tab is hidden', async () => {
		const m = mountForm([mainTab, extrasTab({ visible: false })], { prompt: 'saved', strength: 'from-hidden' });
		await settle();
		expect(m.tabButtons()).toHaveLength(0);
		expect(m.input('prompt')!.value).toBe('saved');
		expect(m.panelOf('prompt').classList.contains('hidden')).toBe(false);
		expect(m.lastData().strength).toBe('from-hidden');
	});

	it('keeps a saved non-first-tab value when the tab is later revealed by a reaction', async () => {
		const m = mountForm([mainTab, reactiveExtrasTab], { prompt: 'saved', strength: 'from-extras', extras_on: false });
		await settle();
		expect(m.tabButtons()).toHaveLength(0);
		await m.toggleExtras();
		expect(m.tabButtons()).toHaveLength(2);
		expect(m.input('strength')!.value).toBe('from-extras');
		await m.clickTab('Extras');
		expect(m.tabButtons()[1].getAttribute('aria-selected')).toBe('true');
	});

	it('replaying an initialData onto a form whose bar appears keeps every value', async () => {
		const m = mountForm([mainTab, reactiveExtrasTab], { prompt: 'a', extras_on: false });
		await settle();
		m.form.$set({ initialData: { prompt: 'b', strength: 'x', extras_on: true } });
		await settle();
		expect(m.tabButtons()).toHaveLength(2);
		expect(m.input('prompt')!.value).toBe('b');
		expect(m.input('strength')!.value).toBe('x');
	});
});
