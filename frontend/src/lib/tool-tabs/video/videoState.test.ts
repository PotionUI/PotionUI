import { describe, expect, it } from 'vitest';
import { apply, beginEdit, createStack } from '$lib/tool-tabs/frame/editStack';
import type { MediaToolRun } from '$lib/media-tools/types';
import { draftFor, type VideoParams } from './videoSteps';
import {
	EMPTY_SIGNATURE,
	directInputRef,
	dirtyCount,
	formatEditorTime,
	parseEditorTime,
	readVideoState,
	settleState,
	writeVideoState
} from './videoState';

describe('video editor state', () => {
	it('round-trips the stack, tool and playhead', () => {
		const stack = apply(beginEdit(createStack<VideoParams>(), draftFor('mute', {})));
		const loaded = readVideoState(
			JSON.parse(
				JSON.stringify(
					writeVideoState({
						stack,
						tool: 'volume',
						playhead: 3.1234,
						loop: false,
						savedSignature: EMPTY_SIGNATURE,
						saved: null,
						renderSignature: 'sig',
						renderMode: 'new',
						renderStartedAt: 5
					})
				)
			)
		);
		expect(loaded.stack.steps.map((step) => step.tool)).toEqual(['mute']);
		expect(loaded.tool).toBe('volume');
		expect(loaded.playhead).toBe(3.123);
		expect(loaded.loop).toBe(false);
		expect(loaded.renderSignature).toBe('sig');
	});

	it('opens empty on anything it does not know', () => {
		const loaded = readVideoState({ v: 99, tool: 'trim' });
		expect(loaded.stack.steps).toEqual([]);
		expect(loaded.tool).toBeNull();
		expect(readVideoState(null).savedSignature).toBe(EMPTY_SIGNATURE);
	});

	it('marks the render snapshot saved and remembers the copy', () => {
		const base = readVideoState(null);
		const completed: MediaToolRun = {
			run_id: 'r',
			status: 'completed',
			progress: 1,
			label: '',
			items: [{ id: 'item-9', filename: 'clip-edit.mp4', url: '/m/clip-edit.mp4', media_type: 'video' }],
			replaced: false,
			error: null
		};
		const settled = settleState({ ...base, renderSignature: 'snap', renderMode: 'new' }, completed);
		expect(settled.savedSignature).toBe('snap');
		expect(settled.saved).toEqual({ itemId: 'item-9', url: '/m/clip-edit.mp4', name: 'clip-edit.mp4', path: 'uploads/clip-edit.mp4' });
		expect(settled.renderSignature).toBeNull();
		const cancelled = settleState({ ...base, renderSignature: 'snap', renderMode: 'new' }, { ...completed, status: 'cancelled', items: [] });
		expect(cancelled.savedSignature).toBe(EMPTY_SIGNATURE);
		expect(cancelled.saved).toBeNull();
	});

	it('counts unsaved edits only when the stack differs from the last save', () => {
		expect(dirtyCount('a', 'a', 3)).toBe(0);
		expect(dirtyCount('b', 'a', 3)).toBe(3);
		expect(dirtyCount('[]', 'a', 0)).toBe(1);
	});
});

describe('editor time', () => {
	it('formats as mm:ss.cc and parses it back', () => {
		expect(formatEditorTime(3.12)).toBe('00:03.12');
		expect(formatEditorTime(75.5)).toBe('01:15.50');
		expect(formatEditorTime(Number.NaN)).toBe('00:00.00');
		expect(parseEditorTime('00:03.12')).toBeCloseTo(3.12, 6);
		expect(parseEditorTime('4.5')).toBe(4.5);
		expect(parseEditorTime('x')).toBeNull();
	});
});

describe('directInputRef', () => {
	it('reads a Library item or a generation file id from the source', () => {
		expect(directInputRef({ path: 'p', item_id: 'u1' } as never)).toEqual({ item_id: 'u1' });
		expect(directInputRef({ path: 'p', generation_file_id: '7' } as never)).toEqual({ generation_file_id: '7' });
		expect(directInputRef({ path: 'uploads/a.mp4' })).toBeNull();
	});
});
