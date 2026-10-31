import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { MessageCoalescer } from '$lib/generation/messageCoalescer';

function tabId(): string {
	return get(tabsStore).tabs[0].id;
}

function seed(id: string, generationId: string) {
	const tab = get(tabsStore).tabs.find((t) => t.id === id)!;
	tabsStore.updateTab(id, {
		generation: { ...tab.generation, currentGeneration: { id: generationId, generation_id: generationId, status: 'running' } }
	});
}

function current(id: string): any {
	return get(tabsStore).tabs.find((t) => t.id === id)!.generation.currentGeneration;
}

function clipMessage(generationId: string, step: number, extra: Record<string, unknown> = {}) {
	return {
		type: 'workbench_preview_clip',
		generation_id: generationId,
		image: 'data:image/webp;base64,AAAA',
		frames: 25,
		columns: 5,
		frame_width: 384,
		frame_height: 216,
		fps: 8,
		step,
		total_steps: 30,
		source_indices: [],
		...extra
	} as any;
}

describe('workbench_preview_clip handler', () => {
	beforeEach(() => tabsStore.reset());

	it('stores the clip on the current generation without touching the single frame', () => {
		const id = tabId();
		seed(id, 'g1');
		dispatchGenerationMessage(clipMessage('g1', 3), { unsubscribe: vi.fn() });
		const gen = current(id);
		expect(gen.preview_clip.step).toBe(3);
		expect(gen.current_image).toBeUndefined();
		expect(gen.status).toBe('running');
	});

	it('replaces the clip in place when a newer one arrives', () => {
		const id = tabId();
		seed(id, 'g1');
		dispatchGenerationMessage(clipMessage('g1', 3), { unsubscribe: vi.fn() });
		dispatchGenerationMessage(clipMessage('g1', 6), { unsubscribe: vi.fn() });
		expect(current(id).preview_clip.step).toBe(6);
		expect(current(id).id).toBe('g1');
	});

	it('drops the clip when the preview is suppressed by the content policy', () => {
		const id = tabId();
		seed(id, 'g1');
		dispatchGenerationMessage(clipMessage('g1', 3), { unsubscribe: vi.fn() });
		dispatchGenerationMessage(
			{ type: 'workbench_preview_clip', generation_id: 'g1', preview_suppressed: true, content_flagged: true } as any,
			{ unsubscribe: vi.fn() }
		);
		expect(current(id).preview_clip).toBeNull();
		expect(current(id).preview_suppressed).toBe(true);
	});

	it('ignores a malformed payload', () => {
		const id = tabId();
		seed(id, 'g1');
		dispatchGenerationMessage(clipMessage('g1', 3, { frames: 0 }), { unsubscribe: vi.fn() });
		expect(current(id).preview_clip).toBeUndefined();
	});

	it('keeps only the newest queued clip per generation in one frame', () => {
		const applied: any[] = [];
		let flush: () => void = () => {};
		const coalescer = new MessageCoalescer<any>((m) => applied.push(m), (f) => (flush = f));
		coalescer.enqueue(clipMessage('g1', 3));
		coalescer.enqueue(clipMessage('g1', 6));
		flush();
		expect(applied.map((m) => m.step)).toEqual([6]);
	});
});
