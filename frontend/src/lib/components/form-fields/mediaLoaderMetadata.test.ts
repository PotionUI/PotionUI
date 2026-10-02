import { describe, it, expect, vi } from 'vitest';
import { fetchMediaMetadata, type MetadataSource } from './mediaLoaderMetadata';

function source(overrides: Partial<MetadataSource> = {}): MetadataSource {
	return {
		listGenerationMedia: vi.fn().mockResolvedValue({ success: false }),
		getUploadInfo: vi.fn().mockResolvedValue({ success: false }),
		...overrides
	};
}

describe('fetchMediaMetadata', () => {
	it('reads an upload by its filename', async () => {
		const getUploadInfo = vi
			.fn()
			.mockResolvedValue({ success: true, data: { width: 640, height: 480, size: 1200, extra: 'x' } });
		const meta = await fetchMediaMetadata('uploads/cat.png', source({ getUploadInfo }));
		expect(getUploadInfo).toHaveBeenCalledWith('cat.png');
		expect(meta).toEqual({ width: 640, height: 480, duration_seconds: undefined, fps: undefined, size: 1200 });
	});

	it('finds a generation file by name among the generation media', async () => {
		const listGenerationMedia = vi.fn().mockResolvedValue({
			success: true,
			data: {
				media: [
					{ filename: '0.png', width: 1, height: 1 },
					{ filename: '1.mp4', width: 1280, height: 720, duration_seconds: 4, fps: 24, size: 99 }
				]
			}
		});
		const meta = await fetchMediaMetadata('outputs/2026-01-01/01KABC/1.mp4', source({ listGenerationMedia }));
		expect(listGenerationMedia).toHaveBeenCalledWith('01KABC');
		expect(meta).toEqual({ width: 1280, height: 720, duration_seconds: 4, fps: 24, size: 99 });
	});

	it('returns null when the generation has no file of that name', async () => {
		const listGenerationMedia = vi
			.fn()
			.mockResolvedValue({ success: true, data: { media: [{ filename: '0.png' }] } });
		expect(await fetchMediaMetadata('outputs/d/01K/9.png', source({ listGenerationMedia }))).toBeNull();
	});

	it('returns null for a failed lookup', async () => {
		expect(await fetchMediaMetadata('uploads/cat.png', source())).toBeNull();
	});

	it('does not ask the server about a temporary file or an unreadable path', async () => {
		const spy = source();
		expect(await fetchMediaMetadata('tmp/scratch.png', spy)).toBeNull();
		expect(await fetchMediaMetadata('', spy)).toBeNull();
		expect(spy.getUploadInfo).not.toHaveBeenCalled();
		expect(spy.listGenerationMedia).not.toHaveBeenCalled();
	});
});
