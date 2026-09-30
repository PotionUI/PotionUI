import { describe, it, expect } from 'vitest';
import {
	orderedRoots,
	bindingsSummary,
	mergeDetectionSuggestions,
	bindingSupportsHeaderScan,
	bindingScanKey,
	bindingRefKey,
	bindingTitle,
	buildBindings,
	detectAgainApplies,
	groupBindingsByType,
	missingSuggestions,
	profileBadgeLabel,
	detectedLabel,
	effectiveWriteChoices,
	groupSuggestionsByType,
	initialTicks,
	initialWriteChoices,
	layoutOptions,
	pendingExtraPaths,
	suggestionKey,
	typesWithChoice
} from './logic';
import type { ModelRootDetectionSuggestion } from '$lib/services/api/models';
import type { ModelRoot, ModelRootDetection } from '$lib/services/api/models';

function root(overrides: Partial<ModelRoot> = {}): ModelRoot {
	return {
		id: 'r1',
		label: 'Library',
		path: '/mnt/storage/models',
		kind: 'library',
		read_only: false,
		case_insensitive: false,
		state: 'online',
		state_reason: null,
		state_checked_at: null,
		bindings: [],
		...overrides
	};
}

function detection(overrides: Partial<ModelRootDetection> = {}): ModelRootDetection {
	return {
		path: '/mnt/storage/models',
		effective_path: '/mnt/storage/models',
		state: 'online',
		writable_hint: true,
		case_insensitive: false,
		layout: 'typed',
		suggestions: [],
		single_type_guess: null,
		conflicts: [],
		warnings: [],
		...overrides
	};
}

describe('orderedRoots', () => {
	it('puts the home root first regardless of input order', () => {
		const library = root({ id: 'lib', kind: 'library' });
		const home = root({ id: 'home', kind: 'home' });

		expect(orderedRoots([library, home]).map((r) => r.id)).toEqual(['home', 'lib']);
	});

	it('leaves relative order alone when there is no home root', () => {
		const a = root({ id: 'a' });
		const b = root({ id: 'b' });

		expect(orderedRoots([a, b]).map((r) => r.id)).toEqual(['a', 'b']);
	});

	it('does not mutate the input array', () => {
		const list = [root({ id: 'b' }), root({ id: 'home', kind: 'home' })];
		const copy = [...list];

		orderedRoots(list);

		expect(list).toEqual(copy);
	});
});

describe('bindingsSummary', () => {
	it('sums indexed files and bytes across all bindings', () => {
		const r = root({
			bindings: [
				{
					model_type: 'lora',
					folder: 'loras',
					subdir: 'loras',
					path: '/mnt/storage/models/loras',
					exists: true,
					position: 0,
					is_write: true,
					indexed_files: 3,
					size_bytes: 1000,
					unindexed: 0,
					scan_headers: false
				},
				{
					model_type: 'checkpoint',
					folder: 'checkpoints',
					subdir: 'checkpoints',
					path: '/mnt/storage/models/checkpoints',
					exists: true,
					position: 0,
					is_write: false,
					indexed_files: 2,
					size_bytes: 500,
					unindexed: 0,
					scan_headers: false
				}
			]
		});

		expect(bindingsSummary(r)).toEqual({ files: 5, bytes: 1500 });
	});

	it('returns zeros for a root with no bindings', () => {
		expect(bindingsSummary(root({ bindings: [] }))).toEqual({ files: 0, bytes: 0 });
	});
});

describe('mergeDetectionSuggestions', () => {
	it('returns null-safe empty list when there is no detection yet', () => {
		expect(mergeDetectionSuggestions(null)).toEqual([]);
	});

	it('passes typed-layout suggestions through unchanged', () => {
		const suggestion = {
			model_type: 'lora',
			subdir: 'loras',
			matched_by: 'canonical' as const,
			file_count: 12,
			file_count_truncated: false
		};

		expect(mergeDetectionSuggestions(detection({ layout: 'typed', suggestions: [suggestion] }))).toEqual([
			suggestion
		]);
	});

	it('synthesizes a single-item suggestion for a single-type layout', () => {
		const result = mergeDetectionSuggestions(
			detection({ layout: 'single', suggestions: [], single_type_guess: 'lora' })
		);

		expect(result).toEqual([
			{ model_type: 'lora', subdir: '', matched_by: 'canonical', file_count: 0, file_count_truncated: false }
		]);
	});

	it('returns an empty list for an empty layout', () => {
		expect(mergeDetectionSuggestions(detection({ layout: 'empty', suggestions: [] }))).toEqual([]);
	});

	it('returns an empty list for a single layout with no type guess', () => {
		expect(
			mergeDetectionSuggestions(detection({ layout: 'single', suggestions: [], single_type_guess: null }))
		).toEqual([]);
	});
});

describe('bindingSupportsHeaderScan', () => {
	it('is true only for checkpoint, diffusion_model and unet bindings', () => {
		for (const model_type of ['checkpoint', 'diffusion_model', 'unet']) {
			expect(bindingSupportsHeaderScan({ model_type })).toBe(true);
		}
		for (const model_type of ['lora', 'vae', 'text_encoder', 'embedding', 'upscaler']) {
			expect(bindingSupportsHeaderScan({ model_type })).toBe(false);
		}
	});
});

describe('bindingScanKey', () => {
	it('separates bindings of one root by type and subdir', () => {
		expect(bindingScanKey('r1', { model_type: 'checkpoint', subdir: 'Stable-diffusion' })).toBe(
			'r1:checkpoint:Stable-diffusion'
		);
		expect(bindingScanKey('r1', { model_type: 'checkpoint', subdir: 'a' })).not.toBe(
			bindingScanKey('r1', { model_type: 'checkpoint', subdir: 'b' })
		);
	});
});

function sug(model_type: string, subdir: string, extra: Partial<ModelRootDetectionSuggestion> = {}): ModelRootDetectionSuggestion {
	return { model_type, subdir, matched_by: 'profile', file_count: 1, file_count_truncated: false, ...extra };
}

const LORA_A = sug('lora', 'Data/Models/Lora', { write: true, scan_headers: false });
const LORA_B = sug('lora', 'Data/Models/LyCORIS', { write: false });
const CKPT = sug('checkpoint', 'Data/Models/StableDiffusion', { write: true, scan_headers: true });

describe('suggestion grouping', () => {
	it('keys a suggestion by type and subdir', () => {
		expect(suggestionKey(LORA_A)).toBe('lora:Data/Models/Lora');
		expect(suggestionKey(LORA_A)).not.toBe(suggestionKey(LORA_B));
	});

	it('groups several folders of one type in first-seen order', () => {
		const groups = groupSuggestionsByType([LORA_A, CKPT, LORA_B]);
		expect(groups.map((g) => g.model_type)).toEqual(['lora', 'checkpoint']);
		expect(groups[0].items.map((i) => i.subdir)).toEqual(['Data/Models/Lora', 'Data/Models/LyCORIS']);
	});

	it('ticks every folder initially', () => {
		expect(initialTicks([LORA_A, LORA_B])).toEqual({
			'lora:Data/Models/Lora': true,
			'lora:Data/Models/LyCORIS': true
		});
	});
});

describe('write choices', () => {
	it('defaults to the folder flagged write, else the first', () => {
		const flaggedSecond = sug('lora', 'b', { write: true });
		expect(initialWriteChoices([sug('lora', 'a'), flaggedSecond])).toEqual({ lora: 'b' });
		expect(initialWriteChoices([sug('vae', 'x'), sug('vae', 'y')])).toEqual({ vae: 'x' });
	});

	it('falls back to the first ticked folder when the chosen one is unticked', () => {
		const ticks = { [suggestionKey(LORA_A)]: false, [suggestionKey(LORA_B)]: true };
		expect(effectiveWriteChoices([LORA_A, LORA_B], ticks, { lora: LORA_A.subdir })).toEqual({
			lora: LORA_B.subdir
		});
	});

	it('leaves out types with nothing ticked', () => {
		expect(effectiveWriteChoices([LORA_A], { [suggestionKey(LORA_A)]: false }, {})).toEqual({});
	});

	it('offers the choice only for types with two or more ticked folders', () => {
		const ticks = initialTicks([LORA_A, LORA_B, CKPT]);
		expect([...typesWithChoice([LORA_A, LORA_B, CKPT], ticks)]).toEqual(['lora']);
		ticks[suggestionKey(LORA_B)] = false;
		expect(typesWithChoice([LORA_A, LORA_B, CKPT], ticks).size).toBe(0);
	});
});

describe('buildBindings', () => {
	const all = [LORA_A, LORA_B, CKPT];

	it('flags exactly one write folder per type', () => {
		const { bindings, writeTypes } = buildBindings(all, initialTicks(all), { lora: LORA_B.subdir }, true);
		expect(bindings.filter((b) => b.write).map((b) => b.subdir)).toEqual([LORA_B.subdir, CKPT.subdir]);
		expect(writeTypes.sort()).toEqual(['checkpoint', 'lora']);
	});

	it('flags nothing when downloads should not go here', () => {
		const { bindings, writeTypes } = buildBindings(all, initialTicks(all), initialWriteChoices(all), false);
		expect(bindings.every((b) => !b.write)).toBe(true);
		expect(writeTypes).toEqual([]);
	});

	it('drops unticked folders and keeps scan_headers only when the server sent it', () => {
		const ticks = { ...initialTicks(all), [suggestionKey(LORA_B)]: false };
		const { bindings } = buildBindings(all, ticks, initialWriteChoices(all), true);
		expect(bindings.map((b) => b.subdir)).toEqual([LORA_A.subdir, CKPT.subdir]);
		expect(bindings[0]).toHaveProperty('scan_headers', false);
		const plain = buildBindings([LORA_B], initialTicks([LORA_B]), {}, true).bindings[0];
		expect(plain).not.toHaveProperty('scan_headers');
	});
});

describe('layoutOptions', () => {
	const catalog = [
		{ id: 'comfyui', label: 'ComfyUI', source: 'marketplace' },
		{ id: 'fooocus', label: 'Fooocus', source: 'marketplace' }
	];

	it('lists the detected profile, alternatives, catalog, then Generic without repeats', () => {
		const options = layoutOptions(
			[
				{ id: 'comfyui', label: 'ComfyUI', confidence: 'weak', score: 3 },
				{ id: 'generic', label: 'Generic (folder names)', confidence: null, score: 0 }
			],
			{ id: 'stabilitymatrix', label: 'StabilityMatrix' },
			catalog
		);
		expect(options.map((o) => o.id)).toEqual(['stabilitymatrix', 'comfyui', 'fooocus', 'generic']);
	});

	it('still ends with Generic when nothing was detected', () => {
		expect(layoutOptions([], null, catalog).map((o) => o.id)).toEqual(['comfyui', 'fooocus', 'generic']);
	});
});

describe('detectedLabel', () => {
	it('reads Detected for strong, Looks like for weak, and a fallback for none', () => {
		expect(detectedLabel({ label: 'ComfyUI', confidence: 'strong' })).toBe('Detected: ComfyUI');
		expect(detectedLabel({ label: 'ComfyUI', confidence: 'weak' })).toBe('Looks like: ComfyUI');
		expect(detectedLabel(null)).toBe('No known layout detected');
	});
});

describe('pendingExtraPaths', () => {
	it('returns ticked extras that are not created yet', () => {
		const extras = [{ path: '/srv/a' }, { path: '/srv/b' }, { path: '/srv/c' }];
		expect(
			pendingExtraPaths(extras, { '/srv/a': true, '/srv/b': true, '/srv/c': false }, { '/srv/a': true })
		).toEqual(['/srv/b']);
	});

	it('never selects extras by default', () => {
		expect(pendingExtraPaths([{ path: '/srv/a' }], {}, {})).toEqual([]);
	});
});

function bnd(model_type: string, subdir: string, extra: Record<string, unknown> = {}) {
	return {
		model_type,
		folder: model_type,
		subdir,
		path: `/srv/x/${subdir}`,
		exists: true,
		position: 0,
		is_write: false,
		indexed_files: 0,
		size_bytes: 0,
		unindexed: 0,
		scan_headers: false,
		...extra
	};
}

describe('groupBindingsByType', () => {
	it('groups several subfolders of one type together', () => {
		const groups = groupBindingsByType([bnd('lora', 'a/Lora'), bnd('vae', 'VAE'), bnd('lora', 'a/LyCORIS')]);
		expect(groups.map((g) => [g.model_type, g.items.length])).toEqual([
			['lora', 2],
			['vae', 1]
		]);
	});
});

describe('bindingTitle and bindingRefKey', () => {
	it('uses the last subdir segment and falls back to the folder name', () => {
		expect(bindingTitle({ subdir: 'Data/Models/LyCORIS', folder: 'loras' })).toBe('LyCORIS');
		expect(bindingTitle({ subdir: 'Data\\Models\\Lora', folder: 'loras' })).toBe('Lora');
		expect(bindingTitle({ subdir: '', folder: 'loras' })).toBe('loras');
	});

	it('keys a binding by root, type and subdir', () => {
		expect(bindingRefKey('r1', { model_type: 'lora', subdir: 'a' })).not.toBe(
			bindingRefKey('r1', { model_type: 'lora', subdir: 'b' })
		);
	});
});

describe('missingSuggestions (detect again only adds)', () => {
	const detected = [
		sug('lora', 'Lora'),
		sug('lora', 'LyCORIS'),
		sug('vae', 'VAE')
	];

	it('returns only folders the root does not have', () => {
		const rootLike = { case_insensitive: false, bindings: [bnd('lora', 'Lora'), bnd('vae', 'VAE')] };
		expect(missingSuggestions(rootLike, detected).map((s) => s.subdir)).toEqual(['LyCORIS']);
	});

	it('treats the same subdir under another type as missing', () => {
		const rootLike = { case_insensitive: false, bindings: [bnd('checkpoint', 'Lora')] };
		expect(missingSuggestions(rootLike, detected)).toHaveLength(3);
	});

	it('ignores slashes and, on a case-insensitive root, case', () => {
		const rootLike = { case_insensitive: true, bindings: [bnd('lora', '/lora/')] };
		expect(missingSuggestions(rootLike, [sug('lora', 'Lora')])).toEqual([]);
		expect(missingSuggestions({ ...rootLike, case_insensitive: false }, [sug('lora', 'Lora')])).toHaveLength(1);
	});

	it('is empty when everything is already added', () => {
		const rootLike = { case_insensitive: false, bindings: [bnd('lora', 'Lora'), bnd('lora', 'LyCORIS'), bnd('vae', 'VAE')] };
		expect(missingSuggestions(rootLike, detected)).toEqual([]);
	});
});

describe('profileBadgeLabel', () => {
	const catalog = [{ id: 'comfyui', label: 'ComfyUI', source: 'marketplace' }];

	it('maps ids to labels, Generic, or the raw id', () => {
		expect(profileBadgeLabel('comfyui', catalog)).toBe('ComfyUI');
		expect(profileBadgeLabel('generic', catalog)).toBe('Generic');
		expect(profileBadgeLabel('gone', catalog)).toBe('gone');
		expect(profileBadgeLabel(null, catalog)).toBe('');
	});
});

describe('detectAgainApplies', () => {
	it('compares the detected root with the stored root path', () => {
		expect(detectAgainApplies({ path: '/srv/sm' }, { path: '/srv/sm', root_path: '/srv/sm/' })).toBe(true);
		expect(detectAgainApplies({ path: '/srv/pinokio' }, { path: '/srv/pinokio', root_path: '/srv/pinokio/api/x' })).toBe(false);
		expect(detectAgainApplies({ path: '/srv/a' }, { path: '/srv/a' })).toBe(true);
	});
});

import {
	breadcrumbs,
	checkFolderAgainstExisting,
	isManualSuggestion,
	manualSuggestion,
	mergeTicks,
	normalizeSubdirInput,
	subdirLabel,
	validateManualFolder,
	withManualSuggestions
} from './logic';

describe('normalizeSubdirInput', () => {
	it('treats blank, dot and ./ as the folder itself', () => {
		for (const value of ['', '   ', '.', './']) {
			expect(normalizeSubdirInput(value)).toEqual({ ok: true, subdir: '' });
		}
	});

	it('cleans separators, dots and edge slashes', () => {
		expect(normalizeSubdirInput(' models\\loras\\ ')).toEqual({ ok: true, subdir: 'models/loras' });
		expect(normalizeSubdirInput('./models//loras/')).toEqual({ ok: true, subdir: 'models/loras' });
		expect(normalizeSubdirInput('a/./b')).toEqual({ ok: true, subdir: 'a/b' });
	});

	it('refuses full paths and drive letters', () => {
		for (const value of ['/srv/models', '\\srv\\models', 'C:\\models', 'd:models']) {
			expect(normalizeSubdirInput(value).ok).toBe(false);
		}
	});

	it('refuses paths that climb out', () => {
		expect(normalizeSubdirInput('..').ok).toBe(false);
		expect(normalizeSubdirInput('a/../b').ok).toBe(false);
		expect(normalizeSubdirInput('a/..').ok).toBe(false);
	});

	it('refuses control characters', () => {
		expect(normalizeSubdirInput('a\u0007b').ok).toBe(false);
	});

	it('keeps names that merely contain dots', () => {
		expect(normalizeSubdirInput('..hidden/v1.5')).toEqual({ ok: true, subdir: '..hidden/v1.5' });
	});
});

describe('checkFolderAgainstExisting', () => {
	const existing = [
		{ model_type: 'lora', subdir: 'models/loras' },
		{ model_type: 'vae', subdir: 'models/vae' }
	];

	it('accepts a sibling folder', () => {
		expect(checkFolderAgainstExisting('models/LyCORIS', 'lora', existing, false)).toBeNull();
	});

	it('names an exact repeat for the same type', () => {
		expect(checkFolderAgainstExisting('models/loras', 'lora', existing, false)).toMatch(/already added/);
	});

	it('refuses the same folder for another type as an overlap', () => {
		expect(checkFolderAgainstExisting('models/loras', 'vae', existing, false)).toMatch(/overlaps models\/loras/);
	});

	it('refuses folders inside and around an added folder', () => {
		expect(checkFolderAgainstExisting('models/loras/sub', 'lora', existing, false)).toMatch(/overlaps/);
		expect(checkFolderAgainstExisting('models', 'checkpoint', existing, false)).toMatch(/overlaps/);
	});

	it('does not confuse names that only share a prefix', () => {
		expect(checkFolderAgainstExisting('models/loras2', 'lora', existing, false)).toBeNull();
	});

	it('refuses the folder itself when anything is added, and anything when it is added', () => {
		expect(checkFolderAgainstExisting('', 'lora', existing, false)).toMatch(/overlaps/);
		expect(checkFolderAgainstExisting('anything', 'lora', [{ model_type: 'lora', subdir: '' }], false)).toMatch(
			/overlaps the folder itself/
		);
	});

	it('honours case only when the root is case-sensitive', () => {
		expect(checkFolderAgainstExisting('Models/Loras', 'lora', existing, false)).toBeNull();
		expect(checkFolderAgainstExisting('Models/Loras', 'lora', existing, true)).toMatch(/already added/);
	});

	it('accepts anything when nothing exists', () => {
		expect(checkFolderAgainstExisting('x', 'lora', [], false)).toBeNull();
		expect(checkFolderAgainstExisting('', 'lora', [], false)).toBeNull();
	});
});

describe('validateManualFolder', () => {
	it('needs a type', () => {
		expect(validateManualFolder('x', '', [], false)).toEqual({ ok: false, error: 'Choose a type first.' });
	});

	it('returns the normalised subdir when valid', () => {
		expect(validateManualFolder('models\\extra', 'lora', [], false)).toEqual({ ok: true, subdir: 'models/extra' });
	});

	it('reports a bad path before an overlap', () => {
		const result = validateManualFolder('../x', 'lora', [{ model_type: 'lora', subdir: 'x' }], false);
		expect(result).toEqual({ ok: false, error: 'A path cannot go up with "..".' });
	});

	it('reports overlaps with existing folders', () => {
		const result = validateManualFolder('a/b', 'lora', [{ model_type: 'vae', subdir: 'a' }], false);
		expect(result.ok).toBe(false);
	});
});

describe('manual suggestions', () => {
	it('builds a manual suggestion labelled by its last segment', () => {
		const suggestion = manualSuggestion('lora', 'models/extra');
		expect(suggestion).toMatchObject({
			model_type: 'lora',
			subdir: 'models/extra',
			label: 'extra',
			source: 'manual',
			matched_by: 'manual',
			write: false
		});
		expect(isManualSuggestion(suggestion)).toBe(true);
		expect(isManualSuggestion({ source: 'profile' })).toBe(false);
		expect(isManualSuggestion({})).toBe(false);
	});

	it('labels the folder itself with an empty label', () => {
		expect(manualSuggestion('lora', '').label).toBe('');
	});

	it('appends manual suggestions after detected ones and skips repeats', () => {
		const detected = [manualSuggestion('lora', 'a')];
		const merged = withManualSuggestions(detected, [manualSuggestion('lora', 'a'), manualSuggestion('vae', 'b')]);
		expect(merged.map((s) => `${s.model_type}:${s.subdir}`)).toEqual(['lora:a', 'vae:b']);
	});

	it('describes the folder itself', () => {
		expect(subdirLabel('')).toBe('the folder itself');
		expect(subdirLabel('a/b')).toBe('a/b');
	});
});

describe('mergeTicks', () => {
	it('keeps earlier choices, ticks new folders and drops removed ones', () => {
		const suggestions = [manualSuggestion('lora', 'a'), manualSuggestion('lora', 'b'), manualSuggestion('vae', 'c')];
		const ticks = mergeTicks({ 'lora:a': false, 'lora:b': true, 'gone:x': true }, suggestions);
		expect(ticks).toEqual({ 'lora:a': false, 'lora:b': true, 'vae:c': true });
	});
});

describe('breadcrumbs', () => {
	it('starts at the folder and accumulates segments', () => {
		expect(breadcrumbs('')).toEqual([{ label: 'Folder', sub: '' }]);
		expect(breadcrumbs('models/loras')).toEqual([
			{ label: 'Folder', sub: '' },
			{ label: 'models', sub: 'models' },
			{ label: 'loras', sub: 'models/loras' }
		]);
	});
});
