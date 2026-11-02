import { describe, expect, it } from 'vitest';
import { apply, beginEdit, createStack, undo } from '$lib/tool-tabs/frame/editStack';
import { imageFieldsOf, sendTargetsFor } from './sendTargets';
import { emptyImageState, readImageState, stackSignature, unsavedEdits, writeImageState } from './state';
import { lookDraft, resizeDraft } from './steps';
import type { GenerationTab } from '$lib/types/tabs';

describe('image tab state', () => {
	it('round-trips the stack, cursor, tool, viewport, mask and saved copy through JSON', () => {
		let stack = apply(beginEdit(createStack(), resizeDraft({ width: 512, height: 512, lock: true })));
		stack = apply(beginEdit(stack, lookDraft({ kind: 'look', filter: 'reel', name: 'Reel', intensity: 60 })));
		stack = undo(stack);
		const written = writeImageState({
			stack,
			activeTool: 'effects',
			viewport: { fit: false, zoom: 2, panX: -10, panY: 4 },
			mask: { path: 'uploads/m.png', url: '/m.png', geometry: '[]' },
			saved: { itemId: 'copy-1', signature: 'x' }
		});
		const read = readImageState(JSON.parse(JSON.stringify(written)));
		expect(read.stack.cursor).toBe(1);
		expect(read.stack.steps.map((step) => step.label)).toEqual(['Resize', 'Reel']);
		expect(read.activeTool).toBe('effects');
		expect(read.viewport).toEqual({ fit: false, zoom: 2, panX: -10, panY: 4 });
		expect(read.mask?.path).toBe('uploads/m.png');
		expect(read.saved).toEqual({ itemId: 'copy-1', signature: 'x' });
	});

	it('lets the host tool win and falls back to an empty state on junk', () => {
		const written = writeImageState({ ...emptyImageState('crop') });
		expect(readImageState(written, 'paint').activeTool).toBe('paint');
		expect(readImageState({ v: 99 }, 'mask')).toEqual(emptyImageState('mask'));
		expect(readImageState(null).viewport.fit).toBe(true);
	});

	it('counts unsaved edits until a save matches the stack', () => {
		const stack = apply(beginEdit(createStack(), resizeDraft({ width: 512, height: 512, lock: true })));
		expect(unsavedEdits(stack, null)).toBe(1);
		expect(unsavedEdits(stack, { itemId: 'c', signature: stackSignature(stack) })).toBe(0);
		expect(unsavedEdits(beginEdit(stack, lookDraft({ kind: 'look', filter: 'n', name: 'Noir', intensity: 50 })), { itemId: 'c', signature: stackSignature(stack) })).toBe(2);
	});
});

const SCHEMA = {
	properties: {
		main: {
			children: [
				{ name: 'prompt', type: 'textarea' },
				{ name: 'reference', type: 'image', title: 'Reference image' },
				{
					name: 'group',
					type: 'group',
					children: [
						{ name: 'source', type: 'media', title: 'Image + mask', accepted_types: ['image'], allow_inpaint: true },
						{ name: 'clip', type: 'media', accepted_types: ['video'] },
						{ name: 'refs', type: 'image', multiple: true }
					]
				}
			]
		}
	}
};

function generationTab(id: string, name: string, preset: string | null, color: string | null = null): GenerationTab {
	return { kind: 'generation', id, name, color, selectedPreset: preset, selectedMode: 'txt2img', prompt: '', negativePrompt: '', formData: {} } as GenerationTab;
}

describe('send targets', () => {
	it('lists single image fields of a form, with their mask support', () => {
		expect(imageFieldsOf(SCHEMA)).toEqual([
			{ name: 'reference', title: 'Reference image', allowInpaint: false },
			{ name: 'source', title: 'Image + mask', allowInpaint: true }
		]);
		expect(imageFieldsOf(null)).toEqual([]);
	});

	it('makes one row per tab and field, marking mask fields while a mask is held', () => {
		const fields = imageFieldsOf(SCHEMA);
		const tabs = [generationTab('t1', 'Portrait study', 'krea2', 'rgb(1 2 3)'), generationTab('t2', 'Empty', null)];
		const targets = sendTargetsFor(tabs, { t1: fields, t2: [] }, { krea2: 'Krea 2' }, true);
		expect(targets.map(({ id, name, detail, badge, color }) => ({ id, name, detail, badge, color }))).toEqual([
			{ id: 't1::reference', name: 'Portrait study', detail: 'Krea 2 · Reference image', badge: undefined, color: 'rgb(1 2 3)' },
			{ id: 't1::source', name: 'Portrait study', detail: 'Krea 2 · Image + mask', badge: 'MASK', color: undefined }
		]);
		const unnamed = sendTargetsFor(tabs, { t1: fields }, {}, false);
		expect(unnamed[1].badge).toBeUndefined();
		expect(unnamed[0].detail).toBe('Reference image');
		const led = sendTargetsFor(tabs, { t1: fields }, {}, false, { tabId: 't1', field: 'source' });
		expect(led.map((target) => target.id)).toEqual(['t1::source', 't1::reference']);
	});
});
