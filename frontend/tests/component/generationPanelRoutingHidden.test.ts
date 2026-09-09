// @vitest-environment jsdom
//
// Maintainer rule (2026-09-08): which backend a generation runs on is admin
// infrastructure - it belongs only in Admin -> Generations (the Routing
// panel) and the persisted run report, never on the Generate page. The panel
// used to render "Runs on <name> - <reason>" in the docked status line from
// a `generation.routingBackend` field; that field (and the wire plumbing
// behind it - status.ts / api.ts / tabs.ts / the start-generation response)
// is gone entirely. This proves a running generation's status line carries
// no backend name or routing text.
import { describe, it, expect, afterEach } from 'vitest';
import type { GenerationState } from '$lib/types/tabs';

const { default: GenerationPanel } = await import('../../src/lib/components/GenerationPanel.svelte');
const { createClassComponent } = await import('svelte/legacy');

function baseGeneration(overrides: Partial<GenerationState> = {}): GenerationState {
	return {
		isGenerating: true,
		currentGeneration: { id: 'gen-1', generation_id: 'gen-1', status: 'running' },
		currentProgress: { step: 'Generating', progress: 0.5, current_step: 'Generating' },
		pipeTimers: {},
		startedAt: Date.now(),
		totalTime: null,
		lastDurationMs: null,
		batchImages: [],
		batchVideos: [],
		batchAudios: [],
		artifacts: [],
		workbenchIndex: 0,
		workbenchTotal: 0,
		queue: [],
		submittedPromptTemplate: null,
		...overrides
	};
}

function mount(generation: GenerationState) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	const component = createClassComponent({
		component: GenerationPanel as never,
		target,
		props: { generation, isGenerating: true, canGenerate: true, presetId: null, tabId: '' }
	});
	return {
		target,
		destroy: () => {
			component.$destroy();
			target.remove();
		}
	};
}

let mounted: ReturnType<typeof mount> | undefined;

afterEach(() => {
	mounted?.destroy();
	mounted = undefined;
});

describe('GenerationPanel status line', () => {
	it('never renders a backend name or routing reason while running', () => {
		mounted = mount(baseGeneration());

		const text = mounted.target.textContent ?? '';
		expect(text).not.toContain('Runs on');
		expect(text).not.toContain('Local Generation');
		expect(text).not.toContain('default backend for this engine');
	});

	it('still renders the running status without a backend segment', () => {
		mounted = mount(baseGeneration());

		const text = mounted.target.textContent ?? '';
		expect(text).toContain('Running');
	});
});
