import { generationTabs } from '$lib/types/tabs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';
import { settle } from './helpers/settle';

const live = vi.hoisted(() => ({ created: 0, disposed: 0 }));

vi.mock('@google/model-viewer', () => ({}));
vi.mock('../../src/routes/generate/components/GenerationFormPane.svelte', async () => ({
	default: (await import('./stubs/FormPaneStub.svelte')).default
}));
vi.mock('../../src/routes/generate/components/PromptSection.svelte', async () => ({
	default: (await import('./stubs/BlankStub.svelte')).default
}));
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
const { tabsStore } = await import('$lib/stores/tabs');
const { mount, unmount, flushSync } = await import('svelte');
const { default: Host } = await import('./stubs/GenerationPanelsHost.svelte');

const modelField = (name: string) => ({
	name,
	type: 'model',
	title: name,
	required: true,
	configuration: { model_type: name }
});

const SCHEMA = {
	properties: {
		generation: {
			type: 'row',
			children: [
				{ name: 'steps', type: 'number', default: 8 },
				modelField('diffusion_model'),
				modelField('text_encoder'),
				modelField('vae')
			]
		}
	}
};

let tabId = '';
let host: ReturnType<typeof mount> | undefined;
let target: HTMLElement | undefined;

function readTab() {
	return generationTabs(get(tabsStore).tabs).find((tab) => tab.id === tabId)!;
}

function mountPanels(patch: Record<string, unknown>) {
	tabId = tabsStore.addTabWithData('Desktop', {
		selectedPreset: 'krea2',
		selectedMode: 'txt2img',
		formData: {},
		...patch
	} as never);
	target = document.body.appendChild(document.createElement('div'));
	host = mount(Host, { target, props: { tabId } });
	flushSync();
}

function update(patch: Record<string, unknown>) {
	tabsStore.updateTab(tabId, patch);
	flushSync();
}

const inlineOrFloatingPanes = () => document.querySelectorAll('[data-form-pane-stub]').length;
const owners = () => inlineOrFloatingPanes() + (live.created - live.disposed);

beforeEach(() => {
	live.created = 0;
	live.disposed = 0;
	clearSchemaCache();
	vi.spyOn(api, 'getPresetFormSchema').mockResolvedValue({
		success: true,
		data: { preset_id: 'krea2', form_schema: SCHEMA }
	} as never);
	vi.spyOn(api, 'getPresetModels').mockImplementation(async (_presetId: string, modelType?: string) => ({
		success: true,
		data: { models: [{ id: `${modelType}-1` }], total: 1 }
	}) as never);
});

afterEach(() => {
	if (host) unmount(host);
	host = undefined;
	target?.remove();
	target = undefined;
	tabsStore.removeTab(tabId);
	vi.restoreAllMocks();
});

describe('desktop form state while the form pane is folded', () => {
	it('fills the defaults and picks the installed models with the pane folded', async () => {
		mountPanels({ leftPanelCollapsed: true });
		await settle(20);

		expect(inlineOrFloatingPanes()).toBe(0);
		expect(readTab().formPublished).toBe(true);
		expect(readTab().formData).toMatchObject({
			steps: 8,
			diffusion_model: 'model:diffusion_model-1',
			text_encoder: 'model:text_encoder-1',
			vae: 'model:vae-1'
		});
	});

	it('keeps exactly one form owner across unfolding, folding and the floating form', async () => {
		mountPanels({ leftPanelCollapsed: false });
		await settle(10);
		expect(inlineOrFloatingPanes()).toBe(1);
		expect(owners()).toBe(1);

		update({ leftPanelCollapsed: true });
		await settle(10);
		expect(inlineOrFloatingPanes()).toBe(0);
		expect(owners()).toBe(1);

		update({ formFloating: true });
		await settle(10);
		expect(inlineOrFloatingPanes()).toBe(1);
		expect(owners()).toBe(1);

		update({ formFloating: false, leftPanelCollapsed: false });
		await settle(10);
		expect(inlineOrFloatingPanes()).toBe(1);
		expect(owners()).toBe(1);
	});
});
