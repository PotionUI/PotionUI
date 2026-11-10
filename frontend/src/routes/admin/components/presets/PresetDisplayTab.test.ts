import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import PresetDisplayTab from './PresetDisplayTab.svelte';
import type { PresetAdminDetail } from '$lib/types/api';

const fileDetail: PresetAdminDetail = {
	preset_id: 'krea2',
	preset_db_id: '01JD7',
	is_mirror: false,
	source_preset_id: null,
	source_name: null,
	mirror_state: null,
	file: { name: 'Krea-2', description: 'The file description', cover: 'public/cover.webp' },
	display: { name: 'Realistic', description: null, cover: null },
	effective: { name: 'Realistic', description: 'The file description', cover: 'public/cover.webp' },
	modes: {
		available: [
			{ name: 'txt2img', label: 'Text to Image' },
			{ name: 'img2img', label: 'Image to Image' }
		],
		offered: null,
		first: null
	},
	mirrors: [],
	assignment_count: 0,
	group_count: 0
};

const mirrorDetail: PresetAdminDetail = {
	...fileDetail,
	preset_id: '01JD8',
	is_mirror: true,
	source_preset_id: 'krea2',
	source_name: 'Realistic',
	mirror_state: 'ok',
	display: { name: 'Film Covers', description: null, cover: 'display/01JD9.webp' },
	effective: { name: 'Film Covers', description: 'The file description', cover: 'display/01JD9.webp' },
	modes: { ...fileDetail.modes, offered: ['txt2img'], first: 'txt2img' }
};

const html = (initialDetail: PresetAdminDetail) =>
	render(PresetDisplayTab, { props: { presetId: initialDetail.preset_id, initialDetail } as any }).body;

describe('PresetDisplayTab', () => {
	it('shows the file values as what an empty field returns to', () => {
		const body = html(fileDetail);
		expect(body).toContain('value="Realistic"');
		expect(body).toContain('placeholder="Krea-2"');
		expect(body).toContain('Leave empty to use “Krea-2”.');
		expect(body).toContain('placeholder="The file description"');
		expect(body).toContain('The preset’s own cover');
		expect(body).not.toContain('data-mirror-modes');
	});

	it('gives a mirror its modes and a custom cover reset', () => {
		const body = html(mirrorDetail);
		expect(body).toContain('data-mirror-modes');
		expect(body).toContain('Modes users can pick');
		expect(body).toContain('Opens first');
		expect(body).toContain('Custom cover');
		expect(body).toContain('Use preset’s cover');
		expect(body).not.toContain('Leave empty to use “');
	});
});
