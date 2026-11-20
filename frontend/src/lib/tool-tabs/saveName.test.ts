import { beforeEach, describe, expect, it } from 'vitest';
import { tabsStore } from '$lib/stores/tabs';
import { saveNameFor } from './saveName';

const history = { path: 'p', relative_path: 'outputs/g/0.png', url: '/u', name: '0.png', title: 'Harbor at dawn', type: 'image' } as never;
const numeric = { path: 'p', relative_path: 'outputs/g/1.mp4', url: '/u', name: '1.mp4', type: 'video' } as never;

describe('saveNameFor', () => {
	beforeEach(() => tabsStore.reset());

	it('uses the source title instead of the stored file number', () => {
		const id = tabsStore.openToolTab('image', history);
		expect(saveNameFor(id, history)).toBe('Harbor at dawn');
	});

	it('has no name for a numbered file without a title', () => {
		const id = tabsStore.openToolTab('video', numeric);
		expect(saveNameFor(id, numeric)).toBeNull();
	});

	it('prefers the name the user gave the tab', () => {
		const id = tabsStore.openToolTab('image', history);
		tabsStore.renameTab(id, 'Cover crop');
		expect(saveNameFor(id, history)).toBe('Cover crop');
	});
});
