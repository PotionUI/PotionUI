import { afterEach, describe, expect, it, vi } from 'vitest';
import { previewFrameUrl, releasePreviewFrame } from './previewFrameUrl';

const PNG_B64 = 'iVBORw0KGgo=';

describe('previewFrameUrl', () => {
	afterEach(() => {
		vi.useRealTimers();
		vi.restoreAllMocks();
	});

	it('turns a data URL into a blob URL carrying the same bytes and type', async () => {
		const created: Blob[] = [];
		vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
			created.push(blob as Blob);
			return `blob:test/${created.length}`;
		});
		const url = previewFrameUrl('tab-a', `data:image/jpeg;base64,${PNG_B64}`);
		expect(url).toBe('blob:test/1');
		expect(created[0].type).toBe('image/jpeg');
		const bytes = new Uint8Array(await created[0].arrayBuffer());
		expect(Array.from(bytes)).toEqual(Array.from(Buffer.from(PNG_B64, 'base64')));
		releasePreviewFrame('tab-a');
	});

	it('treats raw base64 as png', () => {
		const created: Blob[] = [];
		vi.spyOn(URL, 'createObjectURL').mockImplementation((blob) => {
			created.push(blob as Blob);
			return 'blob:test/raw';
		});
		previewFrameUrl('tab-b', PNG_B64);
		expect(created[0].type).toBe('image/png');
		releasePreviewFrame('tab-b');
	});

	it('revokes the previous frame of the same tab after the next one, never another tab', () => {
		vi.useFakeTimers();
		let n = 0;
		vi.spyOn(URL, 'createObjectURL').mockImplementation(() => `blob:test/${++n}`);
		const revoke = vi.spyOn(URL, 'revokeObjectURL').mockImplementation(() => undefined);
		previewFrameUrl('tab-c', PNG_B64);
		previewFrameUrl('tab-d', PNG_B64);
		previewFrameUrl('tab-c', PNG_B64);
		vi.advanceTimersByTime(2000);
		expect(revoke.mock.calls.map((call) => call[0])).toEqual(['blob:test/1']);
		releasePreviewFrame('tab-c');
		releasePreviewFrame('tab-d');
		vi.advanceTimersByTime(2000);
		expect(revoke.mock.calls.map((call) => call[0]).sort()).toEqual(['blob:test/1', 'blob:test/2', 'blob:test/3']);
	});

	it('falls back to a data URL when the payload cannot be decoded', () => {
		expect(previewFrameUrl('tab-e', '***not base64***')).toBe('data:image/png;base64,***not base64***');
	});
});
