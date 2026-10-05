import { describe, expect, it } from 'vitest';
import { readFileSync, readdirSync } from 'node:fs';
import { join } from 'node:path';
import { hasIcon } from '$lib/utils/IconLibrary';
import { actionIcon, factIcon, mediaKindIcon, subjectIcon } from './icons';

const dir = join(__dirname, '../components/organize');
const sources = [
	...readdirSync(dir)
		.filter((f) => f.endsWith('.svelte'))
		.map((f) => join(dir, f)),
	join(__dirname, '../components/detail/DetailSection.svelte')
];

describe('organize icons', () => {
	it('every mapped icon exists in the Icon set', () => {
		const names = [
			...['model', 'lora', 'preset', 'mode', 'media_kind', 'resolution', 'aspect', 'duration', 'prompt', 'filename', 'tags', 'model_type', 'base_model', 'plugin_fact'].map(factIcon),
			...['add_to_collection', 'add_tags', 'plugin_action'].map(actionIcon),
			...(['generation', 'upload', 'model'] as const).map(subjectIcon),
			...['image', 'video', 'audio', 'mesh'].map((k) => mediaKindIcon(k) as string)
		];
		for (const name of names) expect(hasIcon(name), name).toBe(true);
	});

	it('every literal icon name used in organize components exists', () => {
		for (const file of sources) {
			const text = readFileSync(file, 'utf8');
			for (const m of text.matchAll(/(?:icon|name)="([a-z0-9-]+)"/g)) {
				expect(hasIcon(m[1]), `${file}: ${m[1]}`).toBe(true);
			}
		}
	});
});
