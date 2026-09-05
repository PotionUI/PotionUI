import { describe, it, expect } from 'vitest';
import { isMediaRef, resolveArtifactImageSrc, type RunReportMediaRef } from './mediaRef';

const ref: RunReportMediaRef = {
	$media: 'run_report_artifact',
	name: 'runreport_01H.jpg',
	url: '/api/generations/gen-1/run-report/artifacts/runreport_01H.jpg',
	path: 'generations/2026-09-05/gen-1/runreport_01H.jpg',
	bytes: 4096,
	mime: 'image/jpeg',
	width: 768,
	height: 512
};

describe('isMediaRef', () => {
	it('recognises a saved-report reference', () => {
		expect(isMediaRef(ref)).toBe(true);
	});

	it('rejects an inline payload and anything else', () => {
		expect(isMediaRef('iVBORw0KGgo=')).toBe(false);
		expect(isMediaRef({ url: '/api/x' })).toBe(false);
		expect(isMediaRef(null)).toBe(false);
	});
});

describe('resolveArtifactImageSrc', () => {
	it('resolves a saved-report reference against the API base URL', () => {
		expect(resolveArtifactImageSrc(ref, 'http://host:7680')).toBe(
			'http://host:7680/api/generations/gen-1/run-report/artifacts/runreport_01H.jpg'
		);
	});

	it('keeps rendering a live inline base64 payload', () => {
		expect(resolveArtifactImageSrc('iVBORw0KGgo=', 'http://host:7680')).toBe(
			'data:image/png;base64,iVBORw0KGgo='
		);
	});

	it('passes data and absolute URLs through untouched', () => {
		expect(resolveArtifactImageSrc('data:image/jpeg;base64,AAA', 'http://host:7680')).toBe(
			'data:image/jpeg;base64,AAA'
		);
		expect(resolveArtifactImageSrc('https://cdn/x.png', 'http://host:7680')).toBe(
			'https://cdn/x.png'
		);
	});

	it('resolves an already-persisted output path against the API base URL', () => {
		expect(resolveArtifactImageSrc('/api/media/generations/gen-1/0.png', 'http://host:7680')).toBe(
			'http://host:7680/api/media/generations/gen-1/0.png'
		);
	});

	it('returns nothing for an omitted payload', () => {
		expect(resolveArtifactImageSrc(undefined)).toBe('');
		expect(resolveArtifactImageSrc(null)).toBe('');
	});
});
