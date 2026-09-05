// @vitest-environment jsdom
//
// A plugin's `generation.output` message was recorded on the owning tab and
// never rendered: no reachable generation surface mounted the renderer seam.
// These mount the real workbench pane and drive it the way the WebSocket
// does - register the handler the plugin manifest would register, dispatch a
// message through `dispatchGenerationMessage`, and assert the plugin's own
// component reaches the DOM for that tab and that run only.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';

vi.mock('$lib/plugin-api/componentResolver', () => ({
	resolvePluginComponent: vi.fn()
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
		getClient: () => ({ get: vi.fn(), post: vi.fn() })
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
const { tabsStore } = await import('$lib/stores/tabs');
const { dispatchGenerationMessage } = await import('$lib/stores/generation');
const { registerPluginOutputHandler, unregisterPluginOutputHandler } = await import(
	'$lib/generation/messages/pluginOutput'
);
const { default: GenerationWorkbenchPane } = await import(
	'../../src/routes/generate/components/GenerationWorkbenchPane.svelte'
);
const { default: StubOutput } = await import('./fixtures/PluginOutputStubComponent.svelte');
const { default: StaleStub } = await import('./fixtures/PluginSlotStubComponent.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { get } = await import('svelte/store');

const PLUGIN_ID = 'example';
const MESSAGE_TYPE = 'example_output';
const ASSET = 'ExampleOutput.js';
const RELOADED_ASSET = 'ExampleOutput.v2.js';

function deferred<T>() {
	let resolve!: (value: T) => void;
	const promise = new Promise<T>((r) => (resolve = r));
	return { promise, resolve };
}

const pending = new Map<string, ReturnType<typeof deferred<unknown>>>();

/** Settles the load the renderer started for `asset`. */
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

/** Puts `tabId` in the state the page leaves it in right after a Generate click. */
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

/** Re-feeds the pane the way the page's `{#if isActive}` mount sites do. */
function showTab(tabId: string) {
	component!.$set({ tab: get(tabsStore).tabs.find((t) => t.id === tabId)! });
}

function renderedOutputs(): string[] {
	return Array.from(target.querySelectorAll('.stub-plugin-output')).map(
		(el) => el.getAttribute('data-stub-output') ?? ''
	);
}

beforeEach(() => {
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
	registerPluginOutputHandler(MESSAGE_TYPE, PLUGIN_ID, ASSET);
});

afterEach(() => {
	unregisterPluginOutputHandler(MESSAGE_TYPE, PLUGIN_ID);
	component?.$destroy();
	component = undefined;
	target.remove();
	(globalThis as any).ResizeObserver = originalRO;
	tabsStore.reset();
	vi.mocked(resolvePluginComponent).mockReset();
});

describe('plugin generation.output rendering in the workspace', () => {
	it('renders the plugin component for a message dispatched to the mounted tab', async () => {
		const tabId = tabAt(0).id;
		startRun(tabId, 'gen-1');
		sendOutput('gen-1', 'first');
		mountPane(tabId);
		showTab(tabId);

		completeLoad(ASSET, StubOutput);
		await settle();

		expect(renderedOutputs()).toEqual(['first']);
	});

	it('shows only the mounted tab’s outputs', async () => {
		const tabA = tabAt(0).id;
		tabsStore.addTab();
		const tabB = tabAt(1).id;

		startRun(tabA, 'gen-a');
		startRun(tabB, 'gen-b');
		sendOutput('gen-a', 'from-a');
		sendOutput('gen-b', 'from-b');

		mountPane(tabA);
		showTab(tabA);
		completeLoad(ASSET, StubOutput);
		await settle();
		expect(renderedOutputs()).toEqual(['from-a']);

		showTab(tabB);
		await settle();
		expect(renderedOutputs()).toEqual(['from-b']);
	});

	it('drops the previous run’s output when a new run starts', async () => {
		const tabId = tabAt(0).id;
		startRun(tabId, 'gen-1');
		sendOutput('gen-1', 'old');
		mountPane(tabId);
		showTab(tabId);
		completeLoad(ASSET, StubOutput);
		await settle();
		expect(renderedOutputs()).toEqual(['old']);

		startRun(tabId, 'gen-2');
		showTab(tabId);
		await settle();

		expect(renderedOutputs()).toEqual([]);
	});

	it('renders nothing when the plugin is removed while its component is loading', async () => {
		const tabId = tabAt(0).id;
		startRun(tabId, 'gen-1');
		sendOutput('gen-1', 'in-flight');
		mountPane(tabId);
		showTab(tabId);
		await settle();

		unregisterPluginOutputHandler(MESSAGE_TYPE, PLUGIN_ID);
		showTab(tabId);
		await settle();

		completeLoad(ASSET, StubOutput);
		await settle();

		expect(renderedOutputs()).toEqual([]);
		expect(tabAt(0).generation.pluginOutputs?.[MESSAGE_TYPE]).toBeUndefined();
	});

	it('keeps a reloaded renderer when the load it replaced resolves last', async () => {
		const tabId = tabAt(0).id;
		startRun(tabId, 'gen-1');
		sendOutput('gen-1', 'reloaded');
		mountPane(tabId);
		showTab(tabId);
		await settle();
		expect(pending.has(ASSET)).toBe(true);

		registerPluginOutputHandler(MESSAGE_TYPE, PLUGIN_ID, RELOADED_ASSET);
		sendOutput('gen-1', 'reloaded');
		showTab(tabId);
		await settle();

		completeLoad(RELOADED_ASSET, StubOutput);
		await settle();
		expect(renderedOutputs()).toEqual(['reloaded']);

		completeLoad(ASSET, StaleStub);
		await settle();

		expect(renderedOutputs()).toEqual(['reloaded']);
		expect(target.querySelectorAll('.stub-plugin-component')).toHaveLength(0);
	});

	it('falls back to a bounded notice when the component cannot be resolved', async () => {
		const tabId = tabAt(0).id;
		startRun(tabId, 'gen-1');
		sendOutput('gen-1', 'unresolvable');
		mountPane(tabId);
		showTab(tabId);

		completeLoad(ASSET, null);
		await settle();

		const fallback = target.querySelector(`[data-plugin-output-unavailable="${PLUGIN_ID}"]`);
		expect(fallback).not.toBeNull();
		expect(fallback!.textContent).toContain(MESSAGE_TYPE);
		expect(renderedOutputs()).toEqual([]);
	});
});
