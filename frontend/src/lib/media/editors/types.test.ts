import { describe, it, expect } from 'vitest';
import {
	editorTitle,
	editsTheResource,
	hasEditor,
	MEDIA_EDITOR_KINDS,
	RESOURCE_EDIT_TOOLS,
	type MediaEditorKind
} from './types';

const KINDS: readonly MediaEditorKind[] = MEDIA_EDITOR_KINDS;

describe('hasEditor', () => {
	it('offers crop and mask on images only', () => {
		expect(hasEditor('crop', 'image')).toBe(true);
		expect(hasEditor('mask', 'image')).toBe(true);
		expect(hasEditor('crop', 'video')).toBe(false);
		expect(hasEditor('crop', 'audio')).toBe(false);
		expect(hasEditor('mask', 'video')).toBe(false);
		expect(hasEditor('mask', 'audio')).toBe(false);
	});

	it('offers a frame grab on video only — a still has no frames to lift', () => {
		expect(hasEditor('frame', 'video')).toBe(true);
		expect(hasEditor('frame', 'image')).toBe(false);
		expect(hasEditor('frame', 'audio')).toBe(false);
	});

	it('offers trim on both timed media', () => {
		expect(hasEditor('trim', 'video')).toBe(true);
		expect(hasEditor('trim', 'audio')).toBe(true);
		expect(hasEditor('trim', 'image')).toBe(false);
	});

	it('offers split on audio only', () => {
		expect(hasEditor('split', 'audio')).toBe(true);
		expect(hasEditor('split', 'video')).toBe(false);
		expect(hasEditor('split', 'image')).toBe(false);
	});

	it('offers nothing when the kind is unknown', () => {
		for (const kind of KINDS) {
			expect(hasEditor(kind, null)).toBe(false);
		}
	});
});

describe('editorTitle', () => {
	it('names trim after the surface it is performed on', () => {
		// The two names the design uses; the audio one is a waveform, not a rail.
		expect(editorTitle('trim', 'audio')).toBe('Trim on waveform');
		expect(editorTitle('trim', 'video')).toBe('Trim in / out');
	});

	it('falls back to the video wording when the kind is unknown', () => {
		expect(editorTitle('trim', null)).toBe('Trim in / out');
	});

	it('names the kind-independent editors the same however they are opened', () => {
		expect(editorTitle('crop', 'image')).toBe('Crop & frame');
		expect(editorTitle('frame', 'video')).toBe('Extract a frame');
		expect(editorTitle('mask', 'image')).toBe('Create inpainting mask');
		expect(editorTitle('split', 'audio')).toBe('Split into parts');
	});

	it('answers for every kind, so a new one cannot render an empty title', () => {
		for (const kind of KINDS) {
			expect(editorTitle(kind, 'video')).not.toBe('');
		}
	});
});

describe('editsTheResource', () => {
	it('is true for the four that change the media', () => {
		expect(editsTheResource('crop')).toBe(true);
		expect(editsTheResource('trim')).toBe(true);
		expect(editsTheResource('frame')).toBe(true);
		expect(editsTheResource('split')).toBe(true);
	});

	it('is false for a mask, which is stored beside the media and changes no row', () => {
		expect(editsTheResource('mask')).toBe(false);
	});
});

describe('RESOURCE_EDIT_TOOLS', () => {
	it('offers every editor except the mask, which belongs to a form field', () => {
		const offered = new Set(RESOURCE_EDIT_TOOLS.map((tool) => tool.key));
		const expected = KINDS.filter((kind) => kind !== 'mask');

		// Not a spelling of the list back at itself: it is driven by
		// MEDIA_EDITOR_KINDS, so adding an editor and forgetting the library
		// fails here instead of shipping an editor nobody can reach.
		expect([...offered].sort()).toEqual([...expected].sort());
		expect(offered.has('mask')).toBe(false);
	});

	it('gives every tool a label and an icon to render with', () => {
		for (const tool of RESOURCE_EDIT_TOOLS) {
			expect(tool.label).not.toBe('');
			expect(tool.icon).not.toBe('');
		}
	});

	it('reaches audio with both of the editors audio supports', () => {
		const forAudio = RESOURCE_EDIT_TOOLS.filter((tool) => hasEditor(tool.key, 'audio')).map(
			(tool) => tool.key
		);
		expect(forAudio).toEqual(['trim', 'split']);
	});
});
