import { describe, expect, it } from 'vitest';
import type { PresetInfo } from '$lib/types/api';
import { presetsKind } from './kinds';
import { buildCollisionIndex, idTail, normalizeName, pickFacts } from './pickFacts';

function preset(id: string, name: string, extra: Partial<PresetInfo> = {}): PresetInfo {
	return {
		id,
		name,
		version: '1.0.0',
		tags: [],
		engine: 'native',
		origin: { kind: 'marketplace', plugin_id: null, path: name },
		...extra
	};
}

const KREA_NATIVE = preset('01NATIVEKREA2', 'Krea-2', {
	version: '1.1.0',
	origin: { kind: 'marketplace', plugin_id: null, path: 'Krea2' }
});
const KREA_COMFY = preset('01COMFYKREA2', 'Krea 2', {
	engine: 'comfyui',
	version: '1.0.1',
	origin: { kind: 'plugin', plugin_id: 'comfyui-backend', path: 'Krea-2' }
});
const SDXL = preset('01SDXL', 'SDXL');

describe('normalizeName', () => {
	it('ignores case and punctuation so Krea-2 and Krea 2 collide', () => {
		expect(normalizeName('Krea-2')).toBe(normalizeName('Krea 2'));
		expect(normalizeName('Z-Image')).toBe(normalizeName('zImage'));
		expect(normalizeName('Qwen-Image')).toBe(normalizeName('QwenImage'));
	});

	it('keeps different names apart', () => {
		expect(normalizeName('Krea-2')).not.toBe(normalizeName('Krea-3'));
	});
});

describe('buildCollisionIndex', () => {
	const index = buildCollisionIndex([KREA_NATIVE, KREA_COMFY, SDXL], presetsKind);

	it('only indexes rows that share a name', () => {
		expect(index.has(KREA_NATIVE.id)).toBe(true);
		expect(index.has(KREA_COMFY.id)).toBe(true);
		expect(index.has(SDXL.id)).toBe(false);
		expect(index.get(KREA_NATIVE.id)?.size).toBe(2);
	});

	it('records which facts differ inside the group', () => {
		const differing = [...(index.get(KREA_NATIVE.id)?.differing ?? [])].sort();
		expect(differing).toEqual(['engine', 'folder', 'source', 'version']);
		expect(index.get(KREA_NATIVE.id)?.needsIdTail).toBe(false);
	});

	it('does not index across the filtered set: the index depends only on the rows given', () => {
		const solo = buildCollisionIndex([KREA_NATIVE], presetsKind);
		expect(solo.size).toBe(0);
	});

	it('marks the id tail as needed only when nothing differs', () => {
		const twin = preset('01TWINAAAAAA', 'Krea-2', {
			version: '1.1.0',
			origin: { kind: 'marketplace', plugin_id: null, path: 'Krea2' }
		});
		const twins = buildCollisionIndex([KREA_NATIVE, twin], presetsKind);
		expect(twins.get(twin.id)?.differing.size).toBe(0);
		expect(twins.get(twin.id)?.needsIdTail).toBe(true);
	});
});

describe('pickFacts', () => {
	const index = buildCollisionIndex([KREA_NATIVE, KREA_COMFY, SDXL], presetsKind);

	it('shows only base facts for a row without a collision', () => {
		const facts = pickFacts(SDXL, presetsKind, index.get(SDXL.id));
		expect(facts.map((f) => f.key)).toEqual(['engine', 'source']);
		expect(facts.every((f) => !f.emphasis)).toBe(true);
	});

	it('promotes differing facts and emphasises them inside a collision group', () => {
		const native = pickFacts(KREA_NATIVE, presetsKind, index.get(KREA_NATIVE.id));
		const comfy = pickFacts(KREA_COMFY, presetsKind, index.get(KREA_COMFY.id));
		expect(native.map((f) => f.key)).toEqual(['engine', 'source', 'folder', 'version']);
		expect(native.every((f) => f.emphasis)).toBe(true);
		expect(native.find((f) => f.key === 'engine')?.value).toBe('native');
		expect(comfy.find((f) => f.key === 'engine')?.value).toBe('comfyui');
		expect(comfy.find((f) => f.key === 'source')?.value).toBe('plugin: comfyui-backend');
		expect(comfy.find((f) => f.key === 'folder')?.value).toBe('Krea-2');
	});

	it('leaves a non-differing, non-base fact out', () => {
		const a = preset('01A', 'Same Name', { version: '2.0.0', engine: 'native' });
		const b = preset('01B', 'Same-Name', { version: '2.0.0', engine: 'comfyui' });
		const idx = buildCollisionIndex([a, b], presetsKind);
		const facts = pickFacts(a, presetsKind, idx.get(a.id));
		expect(facts.map((f) => f.key)).not.toContain('version');
	});

	it('uses the id tail as the last resort', () => {
		const a = preset('01AAAAAAAAAAAAAAAAAAAAAAAA1', 'Dup');
		const b = preset('01AAAAAAAAAAAAAAAAAAAAAAAA2', 'Dup');
		const idx = buildCollisionIndex([a, b], presetsKind);
		const facts = pickFacts(a, presetsKind, idx.get(a.id));
		expect(facts[facts.length - 1]).toMatchObject({ key: 'id', value: `#${idTail(a.id)}` });
		expect(facts[facts.length - 1].value).toBe('#AAAAA1');
	});

	it('caps the number of facts and keeps the differing ones', () => {
		const native = pickFacts(KREA_NATIVE, presetsKind, index.get(KREA_NATIVE.id), 3);
		expect(native).toHaveLength(3);
		expect(native.every((f) => f.emphasis)).toBe(true);
	});

	it('describes a local import by its source and folder', () => {
		const local = preset('01LOCAL', 'Imported', {
			engine: 'comfyui',
			origin: { kind: 'local', plugin_id: null, path: 'QwenImage/imported' }
		});
		const facts = pickFacts(local, presetsKind, undefined);
		expect(facts.map((f) => f.value)).toEqual(['comfyui', 'local']);
	});
});
