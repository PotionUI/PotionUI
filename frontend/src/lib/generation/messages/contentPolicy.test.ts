import { describe, it, expect, beforeEach, vi } from 'vitest';
import { get } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { resetGenerationOutputsRetirementForTests } from './generationOutputs';

function tabId(): string {
	return get(tabsStore).tabs[0].id;
}

function seed(id: string, extra: Record<string, unknown> = {}) {
	const tab = get(tabsStore).tabs.find((t) => t.id === tabId())!;
	tabsStore.updateTab(tabId(), {
		activeGenerationId: id,
		generation: {
			...tab.generation,
			currentGeneration: { generation_id: id, id, status: 'running', ...extra }
		}
	});
}

function current(): any {
	return get(tabsStore).tabs.find((t) => t.id === tabId())!.generation.currentGeneration;
}

function dispatch(message: Record<string, unknown>) {
	dispatchGenerationMessage(message as any, { unsubscribe: vi.fn() });
}

describe('content policy generation messages', () => {
	beforeEach(() => {
		tabsStore.reset();
		resetGenerationOutputsRetirementForTests();
	});

	it('content_blocked records the blocked count on the owning generation', () => {
		seed('gen-1');
		dispatch({ type: 'content_blocked', generation_id: 'gen-1', pipe_id: 2, blocked_count: 2, total: 4 });
		expect(current().content_blocked).toEqual({ blocked_count: 2, total: 4 });
	});

	it('content_blocked for a generation the tab does not own is ignored', () => {
		seed('gen-1');
		dispatch({ type: 'content_blocked', generation_id: 'gen-other', blocked_count: 1, total: 1 });
		expect(current().content_blocked).toBeUndefined();
	});

	it('a suppressed workbench_update shows no pixels and flags the placeholder', () => {
		seed('gen-2', { current_image: 'data:image/png;base64,AAA' });
		dispatch({
			type: 'workbench_update',
			generation_id: 'gen-2',
			pipe_id: 1,
			preview_suppressed: true,
			file_type: 'image'
		});
		expect(current().preview_suppressed).toBe(true);
		expect(current().current_image).toBeNull();
	});

	it('the rated final clears the placeholder and carries the forced flag', () => {
		seed('gen-3', { preview_suppressed: true });
		dispatch({
			type: 'workbench_update',
			generation_id: 'gen-3',
			image: 'AAAA',
			nsfw: true,
			content_flagged: true
		});
		expect(current().preview_suppressed).toBe(false);
		expect(current().content_flagged).toBe(true);
		expect(current().current_image).toBe('data:image/png;base64,AAAA');
	});

	it('gallery_update keeps per-image content_flagged', () => {
		seed('gen-4');
		dispatch({
			type: 'gallery_update',
			generation_id: 'gen-4',
			images: ['AAAA', 'BBBB'],
			image_urls_list: [
				{ original: '/api/media/generations/gen-4/0.png', content_flagged: true },
				{ original: '/api/media/generations/gen-4/1.png' }
			]
		});
		const batch = get(tabsStore).tabs[0].generation.batchImages as any[];
		expect(batch.map((i) => i.content_flagged)).toEqual([true, false]);
	});

	it('a suppressed gallery_update adds no images but marks the placeholder', () => {
		seed('gen-5');
		dispatch({ type: 'gallery_update', generation_id: 'gen-5', preview_suppressed: true, images: ['AAAA'] });
		expect(get(tabsStore).tabs[0].generation.batchImages ?? []).toEqual([]);
		expect(current().preview_suppressed).toBe(true);
	});

	it.each([
		['banned_prompt', 'Your prompt was refused by this server\'s content policy', 'Rephrase and try again.'],
		['content_blocked', 'Blocked by content policy', 'Ask your administrator.'],
		['content_check_unavailable', 'Content check unavailable', 'Ask your administrator.']
	])('generation_error %s shows the backend message and hint', (code, message, hint) => {
		seed(`gen-${code}`);
		dispatch({
			type: 'generation_error',
			generation_id: `gen-${code}`,
			error_code: code,
			message,
			hint
		});
		expect(current().status).toBe('failed');
		expect(current().message).toBe(message);
		expect(current().hint).toBe(hint);
	});
});
