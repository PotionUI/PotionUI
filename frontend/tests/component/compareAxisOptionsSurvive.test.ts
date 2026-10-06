// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/services/api/index', () => ({
	api: new Proxy({ getPresetFormSchema: vi.fn() } as Record<string, unknown>, {
		get: (target, key: string) => target[key] ?? (target[key] = vi.fn())
	})
}));

vi.mock('$lib/generation/compare/compareApi', () => ({
	fetchGridSettings: vi.fn().mockResolvedValue({ confirm_above: 24, hard_cap: 100 }),
	postGrid: vi.fn(),
	fetchGrid: vi.fn(),
	postRetryFailed: vi.fn(),
	removeGrid: vi.fn()
}));

const { api } = await import('$lib/services/api/index');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { default: DynamicForm } = await import('$lib/components/DynamicForm.svelte');
const { default: CompareDrawer } = await import('$lib/components/compare/CompareDrawer.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { flushSync, mount, unmount } = await import('svelte');
const { formAudienceStore } = await import('$lib/stores/formAudience');
const { tabsStore } = await import('$lib/stores/tabs');
const store = await import('$lib/generation/compare/compareStore.svelte');
const { registerBuiltinFieldComponents } = await import('$lib/fields/builtin');

registerBuiltinFieldComponents();

const SAMPLERS = ['euler', 'dpmpp_2m', 'unipc', 'euler_sde', 'euler_ancestral', 'euler_cfg_pp', 'euler_restart', 'dpmpp_2m_sde', 'dpmpp_3m', 'er_sde', 'res_multistep', 'heun'];
const SCHEDULES = ['shift', 'beta', 'exponential', 'linear_quadratic'];

function reaction(then: Record<string, unknown>) {
	return {
		when: { field: 'speed_profile', operator: 'equals', value: 'turbo', equals: 'turbo' },
		then: { set_visibility: null, set_value: null, set_disabled: null, update_options: null, update_validation: null, set_filter_tags: null, ...then }
	};
}

function krea2Schema() {
	return {
		properties: {
			layout: {
				type: 'tabs',
				children: [
					{
						type: 'tab',
						name: 'advanced_tab',
						label: 'Advanced',
						children: [
							{
								type: 'row',
								children: [
									{
										name: 'sampler',
										type: 'sampler',
										title: 'Sampler',
										default: 'euler',
										audience: 'advanced',
										options: SAMPLERS.map((value) => ({ label: value, value })),
										configuration: { allow_empty: false },
										reactions: [reaction({ set_value: 'euler', set_disabled: false })]
									},
									{
										name: 'schedule',
										type: 'schedule',
										title: 'Schedule',
										default: 'shift',
										audience: 'advanced',
										options: SCHEDULES.map((value) => ({ label: value, value })),
										configuration: { allow_empty: false },
										reactions: [reaction({ set_value: 'shift', set_disabled: false })]
									}
								]
							},
							{ name: 'speed_profile', type: 'select', title: 'Profile', default: 'turbo', options: [{ label: 'turbo', value: 'turbo' }] }
						]
					}
				]
			}
		},
		quantity_fields: []
	};
}

const axisOf = (field: string, values: string[]) => ({
	field,
	type: field,
	label: field,
	values: values.map((value) => ({ value, label: value }))
});

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
	flushSync();
}

let tabId = '';
let cleanups: Array<() => void> = [];
let served: ReturnType<typeof krea2Schema>;
let servedBefore = '';

function mountForm(initialData?: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const form = createClassComponent({
		component: DynamicForm as never,
		target,
		props: { presetId: 'krea2', mode: 'txt2img', tabId, initialData }
	});
	let mounted = true;
	const handle = {
		target,
		unmount() {
			if (!mounted) return;
			mounted = false;
			form.$destroy();
			target.remove();
		},
		async openAdvanced() {
			const tab = Array.from(target.querySelectorAll<HTMLElement>('[role="tab"]')).find((el) => el.textContent?.trim() === 'Advanced');
			tab?.click();
			await settle();
		},
		async options(name: string) {
			const trigger = target.querySelector<HTMLElement>(`[data-field-name="${name}"] input, [data-field-name="${name}"] button`);
			trigger!.click();
			await settle();
			const labels = Array.from(document.querySelectorAll('[role="option"]')).map((el) => el.textContent?.trim());
			return labels;
		},
		async pick(index: number) {
			document.querySelectorAll<HTMLElement>('[role="option"]')[index].click();
			await settle();
		}
	};
	cleanups.push(handle.unmount);
	return handle;
}

function mountDrawer() {
	const drawer = mount(CompareDrawer, {
		target: document.body,
		props: { tabId, canGenerate: true, isGenerating: false, onGenerate: () => {} }
	});
	cleanups.push(() => unmount(drawer));
}

const formDataOf = () => get(tabsStore).tabs.find((tab) => tab.id === tabId)!.formData as Record<string, unknown>;

beforeEach(() => {
	clearSchemaCache();
	tabsStore.reset();
	store.resetCompareStoreForTests();
	formAudienceStore.set('advanced');
	tabId = get(tabsStore).tabs[0].id;
	served = krea2Schema();
	servedBefore = JSON.stringify(served);
	vi.mocked(api.getPresetFormSchema).mockImplementation(
		async () => ({ success: true, data: { preset_id: 'p', form_schema: served } }) as never
	);
});

afterEach(() => {
	for (const fn of cleanups) {
		try {
			fn();
		} catch {}
	}
	cleanups = [];
	document.body.innerHTML = '';
});

describe('sampler and schedule options around the Compare lock', () => {
	it('lists every option on a fresh form and accepts a pick', async () => {
		const form = mountForm();
		await settle();
		await form.openAdvanced();
		expect(await form.options('sampler')).toEqual(SAMPLERS);
		await form.pick(3);
		expect(form.target.querySelector<HTMLInputElement>('[data-field-name="sampler"] input')!.value).toBe('euler_sde');
	});

	it('keeps the options intact while both are axes and lists them again after Compare is turned off', async () => {
		const form = mountForm();
		await settle();
		await form.openAdvanced();
		mountDrawer();
		store.armCompare(tabId);
		store.setAxis(tabId, 'x', axisOf('sampler', SAMPLERS.slice(0, 4)));
		store.setAxis(tabId, 'y', axisOf('schedule', SCHEDULES.slice(0, 3)));
		await settle();
		expect(form.target.querySelector('[data-axis-locked="x"]')).toBeTruthy();
		expect(form.target.querySelector('[data-axis-locked="y"]')).toBeTruthy();
		expect(JSON.stringify(served)).toBe(servedBefore);
		expect(store.getCompareSchema(tabId)).toBe(served);

		store.turnOffCompare(tabId);
		await settle();
		expect(form.target.querySelector('[data-axis-locked]')).toBeNull();
		expect(JSON.stringify(served)).toBe(servedBefore);
		expect(await form.options('sampler')).toEqual(SAMPLERS);
		await form.pick(1);
		expect(form.target.querySelector<HTMLInputElement>('[data-field-name="sampler"] input')!.value).toBe('dpmpp_2m');
		expect(await form.options('schedule')).toEqual(SCHEDULES);
		await form.pick(1);
		expect(form.target.querySelector('[data-field-name="schedule"] button')!.textContent).toContain('beta');
	});

	it('survives swapping the axes before turning Compare off', async () => {
		const form = mountForm();
		await settle();
		await form.openAdvanced();
		mountDrawer();
		store.armCompare(tabId);
		store.setAxis(tabId, 'x', axisOf('sampler', SAMPLERS.slice(0, 4)));
		store.setAxis(tabId, 'y', axisOf('schedule', SCHEDULES.slice(0, 3)));
		store.swapAxes(tabId);
		await settle();
		expect(form.target.querySelector('[data-axis-locked="y"]')?.closest('[data-field-name="sampler"]')).toBeTruthy();
		store.turnOffCompare(tabId);
		await settle();
		expect(await form.options('sampler')).toEqual(SAMPLERS);
		expect(JSON.stringify(served)).toBe(servedBefore);
	});

	it('lists the options after a remount while Compare was armed and again after it is turned off', async () => {
		const first = mountForm();
		await settle();
		await first.openAdvanced();
		store.setCompare(tabId, { armed: true, x: axisOf('sampler', SAMPLERS.slice(0, 2)), y: axisOf('schedule', SCHEDULES.slice(0, 2)) });
		await settle();
		first.unmount();

		const second = mountForm(formDataOf());
		await settle();
		await second.openAdvanced();
		expect(second.target.querySelector('[data-axis-locked="x"]')).toBeTruthy();
		store.turnOffCompare(tabId);
		await settle();
		expect(await second.options('sampler')).toEqual(SAMPLERS);
		expect(JSON.stringify(served)).toBe(servedBefore);
	});

	it('lists the options after the axes are picked by hand in the drawer and Compare is turned off', async () => {
		const form = mountForm();
		await settle();
		await form.openAdvanced();
		mountDrawer();
		store.armCompare(tabId);
		await settle();
		const press = (match: (button: HTMLButtonElement) => boolean) =>
			Array.from(document.querySelectorAll<HTMLButtonElement>('button')).find(match)!.click();
		press((button) => button.getAttribute('aria-label') === 'X axis field');
		await settle();
		document.querySelector<HTMLButtonElement>('[role="option"][data-field="sampler"]')!.click();
		await settle();
		press((button) => /^Add a second field/.test(button.textContent?.trim() ?? ''));
		await settle();
		document.querySelector<HTMLButtonElement>('[role="option"][data-field="schedule"]')!.click();
		await settle();
		expect(form.target.querySelector('[data-axis-locked="x"]')).toBeTruthy();
		expect(form.target.querySelector('[data-axis-locked="y"]')).toBeTruthy();
		press((button) => button.textContent?.trim() === 'Turn off');
		await settle();
		expect(form.target.querySelector('[data-axis-locked]')).toBeNull();
		expect(await form.options('sampler')).toEqual(SAMPLERS);
		expect(JSON.stringify(served)).toBe(servedBefore);
	});
});
