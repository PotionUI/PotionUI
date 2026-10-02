import { describe, it, expect } from 'vitest';
import { chooseFace, usesRowFace, type FaceInput } from './mediaFieldFace';

function input(overrides: Partial<FaceInput> = {}): FaceInput {
	return {
		multiple: false,
		compact: false,
		fill: false,
		compactFullWidth: false,
		width: 352,
		itemCount: 0,
		mixedKinds: false,
		uploading: false,
		...overrides
	};
}

describe('chooseFace', () => {
	it('shows the drop zone for an empty field, single or multi', () => {
		expect(chooseFace(input())).toBe('dropzone');
		expect(chooseFace(input({ multiple: true }))).toBe('dropzone');
	});

	it('shows the inspector for a single field holding something', () => {
		expect(chooseFace(input({ itemCount: 1 }))).toBe('inspector');
		expect(chooseFace(input({ multiple: true, mixedKinds: true }))).toBe('strip');
		expect(chooseFace(input({ mixedKinds: true }))).toBe('dropzone');
	});

	it('shows the strip for a multi field holding something', () => {
		expect(chooseFace(input({ multiple: true, itemCount: 2 }))).toBe('strip');
	});

	it('keeps an empty field on its loaded face while a file arrives', () => {
		expect(chooseFace(input({ uploading: true }))).toBe('inspector');
		expect(chooseFace(input({ multiple: true, uploading: true }))).toBe('strip');
	});

	it('uses the row for every compact host, loaded or empty', () => {
		for (const host of [{ compact: true }, { fill: true }, { compactFullWidth: true }]) {
			expect(chooseFace(input({ ...host }))).toBe('row');
			expect(chooseFace(input({ ...host, itemCount: 2, multiple: true }))).toBe('row');
		}
	});

	it('falls back to the row in a column narrower than 240px', () => {
		expect(chooseFace(input({ width: 183, itemCount: 1 }))).toBe('row');
		expect(chooseFace(input({ width: 239 }))).toBe('row');
		expect(chooseFace(input({ width: 240, itemCount: 1 }))).toBe('inspector');
	});

	it('does not judge the width before it has been measured', () => {
		expect(usesRowFace({ compact: false, fill: false, compactFullWidth: false, width: null })).toBe(false);
		expect(usesRowFace({ compact: false, fill: false, compactFullWidth: false, width: 0 })).toBe(false);
	});
});
