import { generationTabs } from '$lib/types/tabs';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import { get } from 'svelte/store';

vi.mock('$lib/utils/generationSounds', () => ({
	playGenerationCompleteSound: vi.fn(),
	playGenerationErrorSound: vi.fn(),
	unlockGenerationSoundContext: vi.fn()
}));

import { tabsStore } from '$lib/stores/tabs';
import { dispatchGenerationMessage } from '$lib/stores/generation';
import { applyConfirmedCancellations } from '$lib/generation/cancelRetirement';
import { clearDeletedGeneration } from '$lib/generation/deleteTabGeneration';
import { previewFrameUrl, withoutPreviewBlob } from './previewFrameUrl';

const PNG_B64 = 'iVBORw0KGgo=';

function tabId(): string {
	return generationTabs(get(tabsStore).tabs)[0].id;
}

function tab(id: string) {
	return generationTabs(get(tabsStore).tabs).find((t) => t.id === id)!;
}

function showFrame(id: string, generationId: string): string {
	const url = previewFrameUrl(id, PNG_B64);
	tabsStore.updateTab(id, {
		activeGenerationId: generationId,
		generation: { ...tab(id).generation, isGenerating: true, currentGeneration: { generation_id: generationId, id: generationId, status: 'running', current_image: url, file_type: 'image' } as any }
	});
	return url;
}

describe('preview frame blob lifecycle', () => {
	let created = 0;
	let revoke: ReturnType<typeof vi.spyOn>;

	beforeEach(() => {
		vi.useFakeTimers();
		created = 0;
		vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:life/${++created}`);
		revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
		tabsStore.reset();
	});

	afterEach(() => {
		vi.useRealTimers();
		vi.restoreAllMocks();
	});

	const revoked = () => {
		vi.advanceTimersByTime(3000);
		return revoke.mock.calls.map((call) => call[0]);
	};

	it('withoutPreviewBlob clears only blob images', () => {
		expect(withoutPreviewBlob({ current_image: 'blob:x/1' }).current_image).toBeNull();
		expect(withoutPreviewBlob({ current_image: '/api/a.png' }).current_image).toBe('/api/a.png');
		expect(withoutPreviewBlob(null)).toBeNull();
	});

	it('revokes on generation_error and never leaves the revoked blob displayed', () => {
		const id = tabId();
		const url = showFrame(id, 'g-err');
		dispatchGenerationMessage({ type: 'generation_error', generation_id: 'g-err', error: 'boom' } as any, { unsubscribe: vi.fn() });
		expect(revoked()).toEqual([url]);
		expect(tab(id).generation.currentGeneration?.current_image ?? null).toBeNull();
	});

	it('revokes on a completion that carries no output and clears the blob', () => {
		const id = tabId();
		const url = showFrame(id, 'g-done');
		dispatchGenerationMessage({ type: 'generation_complete', generation_id: 'g-done', data: { status: 'completed' } } as any, { unsubscribe: vi.fn() });
		expect(revoked()).toEqual([url]);
		expect(tab(id).generation.currentGeneration?.current_image ?? null).toBeNull();
	});

	it('revokes when a cancellation clears the tab display', () => {
		const id = tabId();
		const url = showFrame(id, 'g-cancel');
		applyConfirmedCancellations(tabsStore as any, id, ['g-cancel'], null);
		expect(revoked()).toEqual([url]);
	});

	it('revokes when the shown generation is deleted', () => {
		const id = tabId();
		const url = showFrame(id, 'g-del');
		clearDeletedGeneration(tabsStore as any, id, 'g-del');
		expect(revoked()).toEqual([url]);
	});

	it('revokes when the tab is closed', () => {
		tabsStore.addTab();
		const id = generationTabs(get(tabsStore).tabs)[1].id;
		const url = previewFrameUrl(id, PNG_B64);
		tabsStore.removeTab(id);
		expect(revoked()).toEqual([url]);
	});
});
