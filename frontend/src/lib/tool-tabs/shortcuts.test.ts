import { describe, expect, it } from 'vitest';
import {
	isFoldedShortcut,
	mediaRefForItem,
	planShortcut,
	shortcutFor,
	shortcutLabel,
	shortcutTargets,
	tabFooter,
	tabLabel,
	uniqueTargets
} from './shortcuts';

const last = (kind: 'image' | 'video') => (kind === 'image' ? 'paint' : 'trim');

describe('image shortcuts', () => {
	const rows: Array<[string, string, Record<string, unknown>?]> = [
		['crop', 'crop'],
		['resize', 'resize'],
		['rotate', 'crop', { orientation: 'rotate' }],
		['flip', 'crop', { orientation: 'flip' }],
		['fit_canvas', 'canvas'],
		['color_key', 'colorKey'],
		['crop_subject', 'subject'],
		['edit-image', 'paint'],
		['apply-look', 'effects']
	];

	for (const [action, activeTool, hint] of rows) {
		it(`opens ${action} on the Image editor with ${activeTool} active`, () => {
			expect(shortcutFor(action, 'image', last)).toEqual({
				tool: 'image',
				activeTool,
				...(hint ? { hint } : {})
			});
		});
	}

	it('previews a recent look on Effects, keeping a colon in its id', () => {
		expect(shortcutFor('look:retro-tape:vhs', 'image', last)).toEqual({
			tool: 'image',
			activeTool: 'effects',
			hint: { look: 'retro-tape:vhs' }
		});
	});

	it('has no shortcut for the video-only tools', () => {
		for (const action of ['trim', 'split', 'speed', 'volume', 'film_grain', 'frame']) {
			expect(shortcutFor(action, 'image', last)).toBeNull();
		}
	});
});

describe('video shortcuts', () => {
	const rows: Array<[string, string, Record<string, unknown>?]> = [
		['trim', 'trim'],
		['split', 'split'],
		['crop', 'crop'],
		['resize', 'resize'],
		['speed', 'speed'],
		['rotate', 'crop', { orientation: 'rotate' }],
		['flip', 'crop', { orientation: 'flip' }],
		['volume', 'volume'],
		['mute', 'mute'],
		['replace_audio', 'replace'],
		['film_grain', 'effects', { effect: 'film_grain' }],
		['fix_colors', 'effects', { effect: 'fix_colors' }],
		['frame', 'frame'],
		['edit-image', 'trim']
	];

	for (const [action, activeTool, hint] of rows) {
		it(`opens ${action} on the Video editor with ${activeTool} active`, () => {
			expect(shortcutFor(action, 'video', last)).toEqual({
				tool: 'video',
				activeTool,
				...(hint ? { hint } : {})
			});
		});
	}

	it('has no shortcut for the image-only tools or a look', () => {
		for (const action of ['fit_canvas', 'color_key', 'crop_subject', 'apply-look', 'look:noir', 'concat', 'cut_finder']) {
			expect(shortcutFor(action, 'video', last)).toBeNull();
		}
	});
});

describe('which media has a shortcut at all', () => {
	it('leaves audio and meshes to the dialogs', () => {
		for (const kind of ['audio', 'mesh', 'document']) {
			expect(shortcutFor('trim', kind, last)).toBeNull();
			expect(shortcutFor('edit-image', kind, last)).toBeNull();
		}
	});

	it('hands the last tool used to E for each kind separately', () => {
		const remembered = (kind: 'image' | 'video') => (kind === 'image' ? 'effects' : 'speed');

		expect(shortcutFor('edit-image', 'image', remembered)?.activeTool).toBe('effects');
		expect(shortcutFor('edit-image', 'video', remembered)?.activeTool).toBe('speed');
	});
});

describe('planShortcut', () => {
	const image = (id: string) => ({ id, kind: 'image' });
	const video = (id: string) => ({ id, kind: 'video' });

	it('opens one tab per item in one group, in selection order', () => {
		const plan = planShortcut('crop', [image('a'), image('b'), image('c')], last)!;

		expect(plan).toHaveLength(1);
		expect(plan[0]).toMatchObject({ tool: 'image', activeTool: 'crop' });
		expect(plan[0].items.map((item) => item.id)).toEqual(['a', 'b', 'c']);
	});

	it('splits a mixed selection into an Image group and a Video group, in the order they appear', () => {
		const plan = planShortcut('crop', [video('v'), image('a'), video('w')], last)!;

		expect(plan.map((group) => [group.tool, group.items.map((item) => item.id)])).toEqual([
			['video', ['v', 'w']],
			['image', ['a']]
		]);
	});

	it('refuses the whole selection when one item has no shortcut', () => {
		expect(planShortcut('trim', [video('v'), image('a')], last)).toBeNull();
		expect(planShortcut('crop', [image('a'), { id: 's', kind: 'audio' }], last)).toBeNull();
	});

	it('refuses an empty selection', () => {
		expect(planShortcut('crop', [], last)).toBeNull();
	});

	it('names the tabs a selection opens', () => {
		expect(shortcutTargets('crop', [image('a')])).toEqual(['image']);
		expect(shortcutTargets('crop', [video('v'), image('a')])).toEqual(['video', 'image']);
		expect(shortcutTargets('trim', [image('a')])).toBeNull();
	});
});

describe('menu copy', () => {
	it('relabels Frame and Volume, and only where a tab opens', () => {
		expect(shortcutLabel('frame', [{ kind: 'video' }])).toBe('Open frame in Image editor');
		expect(shortcutLabel('volume', [{ kind: 'video' }])).toBe('Audio');
		expect(shortcutLabel('volume', [{ kind: 'audio' }])).toBeNull();
		expect(shortcutLabel('crop', [{ kind: 'image' }])).toBeNull();
	});

	it('folds Mute and Replace audio into the one Audio row for a video only', () => {
		expect(isFoldedShortcut('mute', [{ kind: 'video' }])).toBe(true);
		expect(isFoldedShortcut('replace_audio', [{ kind: 'video' }])).toBe(true);
		expect(isFoldedShortcut('volume', [{ kind: 'video' }])).toBe(false);
		expect(isFoldedShortcut('mute', [{ kind: 'audio' }])).toBe(false);
	});

	it('names the tab in the footer, for one item and for many', () => {
		expect(tabFooter(['image'], 1)).toBe('Each opens this picture in an Image editor tab, with that tool ready.');
		expect(tabFooter(['video'], 1)).toBe('Each opens this clip in a Video editor tab, with that tool ready.');
		expect(tabFooter(['image'], 3)).toContain('one Image editor tab per picture');
		expect(tabFooter(['video'], 3)).toContain('one Video editor tab per clip');
		expect(tabFooter(['image', 'video'], 2)).toContain('Image or Video editor');
	});

	it('labels the tab kinds and joins the targets of several rows once', () => {
		expect(tabLabel('image')).toBe('Image editor');
		expect(tabLabel('video')).toBe('Video editor');
		expect(uniqueTargets([['image'], null, ['image', 'video'], undefined])).toEqual(['image', 'video']);
	});
});

describe('mediaRefForItem', () => {
	it('keeps the url as the key and carries the resource ids the editors resolve it by', () => {
		expect(
			mediaRefForItem({ id: 'g:0', kind: 'image', url: '/m/g/0.png', filename: '0.png', generationFileId: '42' })
		).toEqual({ path: '/m/g/0.png', url: '/m/g/0.png', name: '0.png', type: 'image', generation_file_id: '42' });
		expect(
			mediaRefForItem({ id: 'u1', kind: 'video', url: '/m/u1.mp4', filename: 'take.mp4', itemId: 'u1' })
		).toEqual({ path: '/m/u1.mp4', url: '/m/u1.mp4', name: 'take.mp4', type: 'video', item_id: 'u1' });
	});
});
