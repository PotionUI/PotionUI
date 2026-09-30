import { describe, it, expect } from 'vitest';
import {
	orderedRoots,
	bindingsSummary,
	mergeDetectionSuggestions,
	bindingSupportsHeaderScan,
	bindingScanKey
} from './logic';
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
