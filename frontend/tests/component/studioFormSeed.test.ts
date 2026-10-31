import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';

const live = vi.hoisted(() => ({ created: 0, disposed: 0 }));

vi.mock('$lib/form/formStateSeed', async (importOriginal) => {
	const actual = await importOriginal<typeof import('$lib/form/formStateSeed')>();
	return {
		...actual,
		createFormStateSeed: (deps: Parameters<typeof actual.createFormStateSeed>[0]) => {
			const seed = actual.createFormStateSeed(deps);
			live.created += 1;
			return {
				sync: seed.sync,
				dispose: () => {
					live.disposed += 1;
					seed.dispose();
				}
			};
		}
	};
});

const { api } = await import('$lib/services/api/index');
const { clearSchemaCache } = await import('$lib/form/schemaCache');
const { forgetClearedSlots } = await import('$lib/form/modelSlotAutopick');
const { tabsStore } = await import('$lib/stores/tabs');
const { buildModeSwitchPatch } = await import('$lib/utils/modeState');
const { missingModelByTab, missingModelFor } = await import('$lib/generation/requiredModelField');
const { mount, unmount, flushSync } = await import('svelte');
const { default: Host } = await import('./stubs/StudioViewHost.svelte');

const modelField = (name: string, title: string) => ({
	name,
	type: 'model',
	title,
	required: true,
	configuration: { model_type: name }
});

function schemaFor(stepsDefault: number) {
	return {
		properties: {
			generation: {
				type: 'row',
				children: [
					{ name: 'steps', type: 'number', default: stepsDefault },
					modelField('diffusion_model', 'Diffusion Model'),
					modelField('text_encoder', 'Text Encoder'),
					modelField('vae', 'VAE')
				]
			}
		}
	};
}

const SCHEMAS: Record<string, unknown> = { txt2img: schemaFor(8), img2img: schemaFor(20) };
const MODELS: Record<string, string> = { diffusion_model: 'dm-1', text_encoder: 'te-1', vae: 'vae-1' };

function readTab(tabId: string) {
	return get(tabsStore).tabs.find((tab) => tab.id === tabId)!;
}

let tabId = '';
let host: ReturnType<typeof mount> | undefined;
let target: HTMLElement | undefined;

function mountStudio(data: Record<string, unknown> = {}) {
	tabId = tabsStore.addTabWithData('Studio', {
		selectedPreset: 'zimage',
		selectedMode: 'txt2img',
		formData: data
	} as never);
	target = document.body.appendChild(document.createElement('div'));
	host = mount(Host, { target, props: { tabId } });
	flushSync();
}

function switchMode(mode: string) {
	const tab = readTab(tabId);
	tabsStore.updateTab(tabId, { ...buildModeSwitchPatch(tab, tab.selectedMode, mode), selectedMode: mode });
	flushSync();
}

const formSections = () => document.querySelectorAll('section[aria-label="generation_form"]').length;

beforeEach(() => {
	live.created = 0;
	live.disposed = 0;
	clearSchemaCache();
	vi.spyOn(api, 'getPresetFormSchema').mockImplementation(async (_presetId: string, mode: string) => ({
		success: true,
		data: { preset_id: 'zimage', form_schema: SCHEMAS[mode] }
	}) as never);
	vi.spyOn(api, 'getPresetModels').mockImplementation(async (_presetId: string, modelType: string) => ({
		success: true,
		data: { models: [{ id: MODELS[modelType] }], total: 1 }
	}) as never);
	vi.spyOn(api, 'getModelById').mockResolvedValue({ success: false } as never);
});

afterEach(() => {
	if (host) unmount(host);
	host = undefined;
	target?.remove();
	target = undefined;
	tabsStore.removeTab(tabId);
	forgetClearedSlots();
	vi.restoreAllMocks();
});

describe('phone Studio form state with Settings never opened', () => {
	it('fills the defaults and picks the installed models when the preset and mode load', async () => {
		mountStudio();
		await settle(20);

		const tab = readTab(tabId);
		expect(formSections()).toBe(0);
		expect(tab.formPublished).toBe(true);
		expect(tab.formData).toMatchObject({
			steps: 8,
			diffusion_model: 'model:dm-1',
			text_encoder: 'model:te-1',
			vae: 'model:vae-1'
		});
		expect(missingModelFor(get(missingModelByTab), tabId, 'zimage-txt2img-')).toBeNull();
	});

	it('keeps the selection the tab already holds', async () => {
		mountStudio({ diffusion_model: 'model:saved', steps: 12 });
		await settle(20);

		expect(readTab(tabId).formData).toMatchObject({
			steps: 12,
			diffusion_model: 'model:saved',
			text_encoder: 'model:te-1',
			vae: 'model:vae-1'
		});
		expect(vi.mocked(api.getPresetModels).mock.calls.map((call) => call[1])).not.toContain('diffusion_model');
	});

	it('does not reset fields already chosen when the mode switches and comes back', async () => {
		mountStudio();
		await settle(20);
		tabsStore.updateTab(tabId, { formData: { ...readTab(tabId).formData, diffusion_model: 'model:chosen' } });
		flushSync();
		await settle(20);
		expect(readTab(tabId).formData.diffusion_model).toBe('model:chosen');

		switchMode('img2img');
		await settle(20);
		expect(readTab(tabId).formData).toMatchObject({ steps: 20, diffusion_model: 'model:dm-1' });
		const searches = vi.mocked(api.getPresetModels).mock.calls.length;

		switchMode('txt2img');
		await settle(20);
		expect(readTab(tabId).formData).toMatchObject({
			steps: 8,
			diffusion_model: 'model:chosen',
			text_encoder: 'model:te-1',
			vae: 'model:vae-1'
		});
		expect(vi.mocked(api.getPresetModels).mock.calls.length).toBe(searches);
	});

	it('hands the form state to the Settings sheet and takes it back, never with two owners', async () => {
		mountStudio();
		await settle(20);
		expect(live.created - live.disposed).toBe(1);

		target!.querySelector<HTMLButtonElement>('button[aria-label="Settings"]')!.click();
		flushSync();
		await settle(20);
		expect(formSections()).toBe(1);
		expect(live.created - live.disposed).toBe(0);

		document.dispatchEvent(new KeyboardEvent('keydown', { key: 'Escape' }));
		flushSync();
		await settle(20);
		expect(formSections()).toBe(0);
		expect(live.created - live.disposed).toBe(1);
		expect(readTab(tabId).formData).toMatchObject({ diffusion_model: 'model:dm-1', vae: 'model:vae-1' });
	});
});
