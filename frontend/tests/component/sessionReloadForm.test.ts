// @vitest-environment jsdom
import { generationTabs } from '$lib/types/tabs';
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { settle } from './helpers/settle';

vi.mock('$lib/services/api/index', () => ({
	api: { getPresetFormSchema: vi.fn() }
}));
vi.mock('$lib/services/cloudCapabilities', () => ({
	fetchCloudCapabilities: vi.fn()
}));

const { api } = await import('$lib/services/api/index');
const { fetchCloudCapabilities } = await import('$lib/services/cloudCapabilities');
const { registerFieldComponent } = await import('$lib/fields/registry');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { sharedCapabilityCache } = await import('$lib/form/capabilityTracker');
const { default: SelectField } = await import('$lib/components/form-fields/SelectField.svelte');
const { default: RowField } = await import('$lib/components/form-fields/RowField.svelte');
const { default: CloudModelStub } = await import('./stubs/CloudModelStub.svelte');
const { mount, unmount } = await import('svelte');
const { get } = await import('svelte/store');
const { tabsStore } = await import('$lib/stores/tabs');
const { default: Host } = await import('./stubs/GenerationFormPaneHost.svelte');

Element.prototype.scrollIntoView = () => {};
registerFieldComponent('row', { component: RowField });
registerFieldComponent('model', { component: CloudModelStub });
registerFieldComponent('select', { component: SelectField });

const SCHEMA = {
	properties: {
		generation: {
			type: 'row',
			children: [
				{ name: 'model', type: 'model', title: 'Model', default: 'model:a', configuration: { model_type: 'cloud', tasks: ['txt2img'] } },
				{
					name: 'aspect_ratio',
					type: 'select',
					title: 'Aspect ratio',
					default: '1:1',
					options: [
						{ label: 'Square', value: '1:1' },
						{ label: 'Wide', value: '16:9' }
					],
					capability: { model_field: 'model', param: 'aspect_ratio' }
				}
			]
		}
	}
};

let tabId = '';
let host: ReturnType<typeof mount> | undefined;
let target: HTMLElement;

function tab() {
	return generationTabs(get(tabsStore).tabs).find((candidate) => candidate.id === tabId)!;
}

function field(name: string) {
	return target.querySelector<HTMLElement>(`[data-field-name="${name}"]`);
}

async function choose(label: string) {
	field('aspect_ratio')!.querySelector<HTMLButtonElement>('button[aria-haspopup="listbox"]')!.click();
	await settle(10);
	Array.from(document.querySelectorAll<HTMLElement>('[role="option"]'))
		.find((el) => el.textContent?.trim() === label)!
		.click();
	await settle(10);
}

beforeEach(() => {
	clearSchemaCache();
	sharedCapabilityCache.clear();
	vi.mocked(api.getPresetFormSchema).mockResolvedValue({ success: true, data: { preset_id: 'p', form_schema: SCHEMA } } as never);
	vi.mocked(fetchCloudCapabilities).mockResolvedValue({
		model_id: 'a',
		params: [{ name: 'aspect_ratio', kind: 'enum', values: ['1:1', '16:9'], default: '1:1' }],
		inputs: []
	} as never);
	target = document.createElement('div');
	document.body.appendChild(target);
	tabId = tabsStore.addTabWithData('Reload form', {
		selectedPreset: `cloud-${Math.random()}`,
		selectedMode: 'txt2img',
		formData: { aspect_ratio: '1:1' }
	} as never);
	host = mount(Host, { target, props: { tabId } });
});

afterEach(() => {
	if (host) unmount(host);
	host = undefined;
	tabsStore.removeTab(tabId);
	target.remove();
	vi.clearAllMocks();
});

describe('Reloading a snapshot into a capability-bound form', () => {
	it('shows the snapshot values again after the user edited them', async () => {
		await settle(12);
		const snapshot = { ...tab().formData };
		expect(snapshot.aspect_ratio).toBe('1:1');

		await choose('Wide');
		expect(tab().formData?.aspect_ratio).toBe('16:9');
		expect(field('aspect_ratio')!.textContent).toContain('Wide');

		tabsStore.updateTab(tabId, { formData: snapshot, formReloadGeneration: (tab().formReloadGeneration ?? 0) + 1 });
		await settle(12);

		expect(tab().formData?.aspect_ratio).toBe('1:1');
		expect(field('aspect_ratio')!.textContent).toContain('Square');
	});

	it('keeps ignoring late echoes after a bump, so the guard stays armed for the life of the form', async () => {
		await settle(12);
		const modelSelect = () => target.querySelector<HTMLSelectElement>('select[aria-label="Model"]')!;
		async function chooseModel(value: string) {
			modelSelect().value = value;
			modelSelect().dispatchEvent(new Event('change', { bubbles: true }));
			await settle(12);
		}

		tabsStore.updateTab(tabId, { formData: { ...tab().formData }, formReloadGeneration: 1 });
		await settle(12);
		await chooseModel('model:b');
		const stale = { ...tab().formData };
		expect(stale.model).toBe('model:b');
		await chooseModel('model:a');

		tabsStore.updateTab(tabId, { formData: stale });
		await settle(12);

		expect(modelSelect().value).toBe('model:a');
	});

	it('stays on the edit when the same snapshot arrives without a reload signal', async () => {
		await settle(12);
		const snapshot = { ...tab().formData };
		await choose('Wide');

		tabsStore.updateTab(tabId, { formData: snapshot });
		await settle(12);

		expect(field('aspect_ratio')!.textContent).toContain('Wide');
	});
});
