import { describe, expect, it, vi } from 'vitest';
import { apply, beginEdit } from '$lib/tool-tabs/frame/editStack';
import { clipAspect, clipFacts, formatClipDuration, resolveClipSourceRef, videoContentKey, videoEditDoc } from './videoEdit';
import { readVideoState, writeVideoState } from './videoState';

function withTrim(end: number) {
	const base = readVideoState(null);
	const stack = apply(beginEdit(base.stack, { id: 'trim', tool: 'trim', label: 'Trim', params: { start: 0, end } } as never));
	return { ...base, stack };
}

describe('videoEditDoc', () => {
	it('keeps the steps and drops the render bookkeeping of the tab', () => {
		const state = { ...withTrim(4), renderSignature: 'sig', renderMode: 'new', renderStartedAt: 99 };
		const doc = videoEditDoc(state, null);
		expect(doc.renderSignature).toBeNull();
		expect(doc.renderMode).toBeNull();
		expect(doc.renderStartedAt).toBeNull();
		expect(readVideoState(doc).stack.steps).toHaveLength(1);
		expect('clip' in doc).toBe(false);
	});

	it('carries the clip facts the list shows', () => {
		const doc = videoEditDoc(readVideoState(null), { duration_seconds: 8, width: 640, height: 360 });
		expect(doc.clip).toEqual({ duration_seconds: 8, width: 640, height: 360 });
	});
});

describe('videoContentKey', () => {
	it('changes with the steps and ignores the playhead, the tool, the loop and the render run', () => {
		const base = withTrim(4);
		const key = videoContentKey(writeVideoState(base) as never);
		const moved = { ...base, playhead: 3, tool: 'crop', loop: false, renderSignature: 'x', renderMode: 'new', renderStartedAt: 5 };
		expect(videoContentKey(writeVideoState(moved) as never)).toBe(key);
		expect(videoContentKey(writeVideoState(withTrim(5)) as never)).not.toBe(key);
	});

	it('sees the saved copy', () => {
		const base = withTrim(4);
		const saved = { ...base, saved: { itemId: 'i1', url: '/u', name: 'n', path: 'uploads/n' }, savedSignature: 'sig' };
		expect(videoContentKey(writeVideoState(saved) as never)).not.toBe(videoContentKey(writeVideoState(base) as never));
	});

	it('ignores the clip facts and the edit link', () => {
		const doc = videoEditDoc(withTrim(4), null);
		expect(videoContentKey({ ...doc, clip: { duration_seconds: 9 }, edit: { id: 'ed', revision: 2 } })).toBe(videoContentKey(doc));
	});
});

describe('clip facts', () => {
	it('keeps only finite positive numbers and rounds the size', () => {
		expect(clipFacts(12.34567, 1920.4, 1080)).toEqual({ duration_seconds: 12.346, width: 1920, height: 1080 });
		expect(clipFacts(0, -1, 5)).toBeNull();
		expect(clipFacts(Number.NaN, 640, 0)).toBeNull();
		expect(clipFacts(5, 640, undefined)).toEqual({ duration_seconds: 5 });
	});

	it('gives the poster aspect only when both sides are known', () => {
		expect(clipAspect({ width: 1080, height: 1920 })).toBeCloseTo(0.5625);
		expect(clipAspect({ width: 1080 })).toBeNull();
		expect(clipAspect(null)).toBeNull();
	});

	it('formats a duration as minutes and seconds, with hours only when needed', () => {
		expect(formatClipDuration(0.2)).toBe('0:01');
		expect(formatClipDuration(12.4)).toBe('0:12');
		expect(formatClipDuration(75)).toBe('1:15');
		expect(formatClipDuration(3725)).toBe('1:02:05');
		expect(formatClipDuration(null)).toBe('');
		expect(formatClipDuration(0)).toBe('');
	});
});

describe('resolveClipSourceRef', () => {
	it('opens a history clip by its file id', async () => {
		const ref = await resolveClipSourceRef({ kind: 'history', id: '42', name: 'rain.mp4' }, { getLibraryItem: vi.fn() });
		expect(ref).toMatchObject({ type: 'video', generation_file_id: '42', name: 'rain.mp4', url: '/api/media/files/42' });
	});

	it('opens a library clip with its size and length so the tab can lay out before it loads', async () => {
		const getLibraryItem = vi.fn(async () => ({
			success: true,
			data: { id: 'it1', filename: 'a.mp4', original_filename: 'Dock.mp4', url: '/api/media/uploads/a.mp4', width: 1280, height: 720, duration_seconds: 9.5, fps: 24 }
		}));
		const ref = await resolveClipSourceRef({ kind: 'library', id: 'it1' }, { getLibraryItem: getLibraryItem as never });
		expect(ref).toMatchObject({ type: 'video', item_id: 'it1', name: 'Dock.mp4', path: 'uploads/a.mp4' });
		expect((ref as { metadata?: unknown }).metadata).toEqual({ width: 1280, height: 720, duration_seconds: 9.5, fps: 24 });
	});

	it('is null when the library item is gone or the lookup fails', async () => {
		const gone = { getLibraryItem: vi.fn(async () => ({ success: false })) } as never;
		const broken = { getLibraryItem: vi.fn(async () => Promise.reject(new Error('404'))) } as never;
		expect(await resolveClipSourceRef({ kind: 'library', id: 'x' }, gone)).toBeNull();
		expect(await resolveClipSourceRef({ kind: 'upload', id: 'x' }, broken)).toBeNull();
	});
});
