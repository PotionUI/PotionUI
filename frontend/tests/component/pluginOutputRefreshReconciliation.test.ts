// @vitest-environment jsdom
//
// The joint seam between the extension refresh and a plugin's stored
// `generation.output`. A disposer is a real teardown - it drops the output
// messages already sitting on every tab - so a refresh that rebuilt its
// registrations wholesale deleted a live plugin's rendered output whenever
// anything else about the plugin set changed. These drive the real
// `refreshPluginExtensions()` against a mounted workbench pane.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

const mockGet = vi.fn();

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn(),
	setPluginRevisions: vi.fn()
}));

vi.mock('$lib/services/api/index', () => ({
	api: {
		getGenerationById: vi.fn(),
		getGenerationParams: vi.fn(),
		updateGenerationTags: vi.fn(),
		getBaseURL: () => 'http://localhost',
		getToken: () => null,
		setOnAuthExpired: vi.fn(),
		getTags: vi.fn().mockResolvedValue({ success: true, data: { tags: [] } }),
		getClient: () => ({ get: mockGet, post: vi.fn() })
	}
}));

vi.mock('$app/stores', () => {
	const { writable } = require('svelte/store');
	return { page: writable({ url: new URL('http://localhost/generate') }) };
});

vi.mock('@google/model-viewer', () => ({}));

class FakeResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}

const { resolvePluginComponent } = await import('$lib/plugin-api/componentResolver');
const { refreshPluginExtensions } = await import('$lib/plugin-api/extensionRefresh');
const { tabsStore } = await import('$lib/stores/tabs');
const { dispatchGenerationMessage } = await import('$lib/stores/generation');
const { default: GenerationWorkbenchPane } = await import(
	'../../src/routes/generate/components/GenerationWorkbenchPane.svelte'
);
const { default: StubOutput } = await import('./fixtures/PluginOutputStubComponent.svelte');
const { default: StaleStub } = await import('./fixtures/PluginSlotStubComponent.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { get } = await import('svelte/store');

const PLUGIN_A = 'plugin-a';
const PLUGIN_B = 'plugin-b';
const MESSAGE_TYPE = 'a_output';
const ASSET = 'AOutput.js';
const REVISED_ASSET = 'AOutput.v2.js';

const RENDERER_A = {
	plugin_id: PLUGIN_A,
	kind: 'generation.output',
	key: MESSAGE_TYPE,
	component: ASSET
};
const RENDERER_B = {
	plugin_id: PLUGIN_B,
	kind: 'history.artifact',
	key: 'b_artifact',
	component: 'BArtifact.js'
};

/** Answers the six catalogues one refresh reads. */
function serveSnapshot(renderers: unknown[], revisions: Record<string, string>) {
	const bodies: Record<string, unknown> = {
		'/api/plugins/frontend-extensions': { renderers, contributions: [], revisions },
		'/api/fields/types': [],
		'/api/plugins/pages': [],
		'/api/plugins/quick-actions': [],
		'/api/plugins/sidebar-widgets': [],
		'/api/plugins/hooks/frontend': {}
	};
	mockGet.mockImplementation(async (path: string) => ({
		data: { success: true, data: bodies[path] }
	}));
}

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

const pending = new Map<string, ReturnType<typeof deferred<unknown>>>();

function completeLoad(asset: string, resolved: unknown) {
	const entry = pending.get(asset);
	if (!entry) throw new Error(`no pending load for ${asset}`);
	entry.resolve(resolved);
}

async function settle() {
	for (let i = 0; i < 10; i++) await new Promise((resolve) => setTimeout(resolve, 0));
}

let originalRO: unknown;
let target: HTMLElement;
let component: { $destroy: () => void; $set: (props: Record<string, unknown>) => void } | undefined;

function tabAt(index: number) {
	return get(tabsStore).tabs[index];
}

function startRun(tabId: string, generationId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	tabsStore.updateTab(tabId, {
		activeGenerationId: generationId,
		generation: {
			...tab.generation,
			isGenerating: true,
			currentGeneration: { id: generationId, generation_id: generationId, status: 'running' }
		}
	});
}

function sendOutput(generationId: string, label: string) {
	dispatchGenerationMessage(
		{ type: MESSAGE_TYPE, generation_id: generationId, label } as never,
		{ unsubscribe: vi.fn() }
	);
}

function mountPane(tabId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId)!;
	component = createClassComponent({
		component: GenerationWorkbenchPane as never,
		target,
		props: {
			tab,
			onWorkbenchPrevious: () => {},
			onWorkbenchNext: () => {},
			onWorkbenchHeightChange: () => {},
			onMoveToWorkbench: () => {}
		}
	}) as never;
	return component!;
}

function showTab(tabId: string) {
	component!.$set({ tab: get(tabsStore).tabs.find((t) => t.id === tabId)! });
}

function renderedOutputs(): string[] {
	return Array.from(target.querySelectorAll('.stub-plugin-output')).map(
		(el) => el.getAttribute('data-stub-output') ?? ''
	);
}

/** Brings plugin A's output on screen for a running generation. */
async function showPluginAOutput(tabId: string, label: string) {
	startRun(tabId, 'gen-1');
	sendOutput('gen-1', label);
	mountPane(tabId);
	showTab(tabId);
	completeLoad(ASSET, StubOutput);
	await settle();
}

beforeEach(async () => {
	originalRO = (globalThis as any).ResizeObserver;
	(globalThis as any).ResizeObserver = FakeResizeObserver;

	target = document.createElement('div');
	document.body.appendChild(target);

	tabsStore.reset();
	pending.clear();
	vi.mocked(resolvePluginComponent).mockImplementation((_pluginId: string, asset: string) => {
		let entry = pending.get(asset);
		if (!entry) {
			entry = deferred<unknown>();
			pending.set(asset, entry);
		}
		return entry.promise as Promise<any>;
	});

	serveSnapshot([RENDERER_A], { [PLUGIN_A]: 'rev-1' });
	await refreshPluginExtensions();
});

afterEach(async () => {
	serveSnapshot([], {});
	await refreshPluginExtensions();
	component?.$destroy();
	component = undefined;
	target.remove();
	(globalThis as any).ResizeObserver = originalRO;
	tabsStore.reset();
	vi.mocked(resolvePluginComponent).mockReset();
	mockGet.mockReset();
});

describe('refreshing plugin extensions around a live plugin output', () => {
	it('keeps a rendered output across an identical refresh', async () => {
		const tabId = tabAt(0).id;
		await showPluginAOutput(tabId, 'live');
		expect(renderedOutputs()).toEqual(['live']);

		await refreshPluginExtensions();
		showTab(tabId);
		await settle();

		expect(renderedOutputs()).toEqual(['live']);
	});

	it('keeps a rendered output when an unrelated plugin is enabled', async () => {
		const tabId = tabAt(0).id;
		await showPluginAOutput(tabId, 'live');

		serveSnapshot([RENDERER_A, RENDERER_B], { [PLUGIN_A]: 'rev-1', [PLUGIN_B]: 'rev-1' });
		await refreshPluginExtensions();
		showTab(tabId);
		await settle();

		expect(renderedOutputs()).toEqual(['live']);
	});

	it('keeps a rendered output when an unrelated plugin is disabled', async () => {
		const tabId = tabAt(0).id;
		serveSnapshot([RENDERER_A, RENDERER_B], { [PLUGIN_A]: 'rev-1', [PLUGIN_B]: 'rev-1' });
		await refreshPluginExtensions();
		await showPluginAOutput(tabId, 'live');
		expect(renderedOutputs()).toEqual(['live']);

		serveSnapshot([RENDERER_A], { [PLUGIN_A]: 'rev-1' });
		await refreshPluginExtensions();
		showTab(tabId);
		await settle();

		expect(renderedOutputs()).toEqual(['live']);
	});

	it('drops the output when its own plugin is disabled', async () => {
		const tabId = tabAt(0).id;
		await showPluginAOutput(tabId, 'live');

		serveSnapshot([], {});
		await refreshPluginExtensions();
		showTab(tabId);
		await settle();

		expect(renderedOutputs()).toEqual([]);
		expect(tabAt(0).generation.pluginOutputs?.[MESSAGE_TYPE]).toBeUndefined();
	});

	it('drops the output when its own plugin is revised, and a late load cannot bring it back', async () => {
		const tabId = tabAt(0).id;
		startRun(tabId, 'gen-1');
		sendOutput('gen-1', 'live');
		mountPane(tabId);
		showTab(tabId);
		await settle();
		expect(pending.has(ASSET)).toBe(true);

		serveSnapshot([{ ...RENDERER_A, component: REVISED_ASSET }], { [PLUGIN_A]: 'rev-2' });
		await refreshPluginExtensions();
		showTab(tabId);
		await settle();

		expect(renderedOutputs()).toEqual([]);
		expect(tabAt(0).generation.pluginOutputs?.[MESSAGE_TYPE]).toBeUndefined();

		completeLoad(ASSET, StaleStub);
		await settle();

		expect(renderedOutputs()).toEqual([]);
		expect(target.querySelectorAll('.stub-plugin-component')).toHaveLength(0);
	});
});
