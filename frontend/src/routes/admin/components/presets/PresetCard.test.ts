import { describe, it, expect } from 'vitest';
import { render } from 'svelte/server';
import PresetCard from './PresetCard.svelte';
import type { PresetInfo } from '$lib/types/api';

const base: PresetInfo = {
	id: 'krea2',
	name: 'Krea-2',
	version: '1.0.0',
	tags: [],
	category: 'image',
	engine: 'native',
	installed: true
};

const html = (props: Record<string, unknown>) =>
	render(PresetCard, { props: { preset: base, onOpen: () => {}, onInstall: () => {}, ...props } as any }).body;

describe('PresetCard mirrors', () => {
	it('marks a mirror with its source’s name', () => {
		const body = html({ preset: { ...base, id: 'm1', name: 'Anime', is_mirror: true, mirror_state: 'ok', source_preset_id: 'krea2' }, sourceName: 'Realistic' });
		expect(body).toContain('Mirror of Realistic');
		expect(body).toContain('data-preset-mirror="ok"');
		expect(body).not.toContain('data-mirror-state-badge');
	});

	it('shows a broken mirror’s state in plain words', () => {
		const body = html({
			preset: { ...base, id: 'm2', name: 'Old one', version: '', is_mirror: true, mirror_state: 'source_missing', source_preset_id: 'gone', media: null as any },
			sourceName: 'gone'
		});
		expect(body).toContain('Source missing');
		expect(body).toContain('data-mirror-state-text');
		expect(body).not.toContain('source_missing<');
		expect(body).not.toContain('>v<');
		expect(body).toContain('Open');
	});

	it('shows how many mirrors a source has', () => {
		expect(html({ preset: { ...base, mirror_count: 2 } })).toContain('2 mirrors');
		expect(html({ preset: { ...base, mirror_count: 0 } })).not.toContain('data-preset-mirror-count');
	});

	it('leaves a plain preset unmarked', () => {
		const body = html({});
		expect(body).not.toContain('Mirror of');
		expect(body).not.toContain('data-preset-mirror=');
	});
});
