// @vitest-environment jsdom
//
// The probe decides whether a file is admitted before anything is uploaded, so
// its failure behaviour is a product decision rather than an implementation
// detail: it RESOLVES EMPTY on every failure instead of rejecting, because a
// codec the browser cannot decode is not a reason to block an upload the
// backend may well accept.
//
// The other property pinned here is the object URL. Every exit path must revoke
// it - a leaked handle per probed file is exactly the class of bug this field
// already shipped once.
import { describe, it, expect, vi, beforeEach, afterEach } from 'vitest';
import { probeMediaFile } from './mediaLoaderProbe';

type Handler = (() => void) | null;

interface FakeMedia {
	onload: Handler;
	onerror: Handler;
	onloadedmetadata: Handler;
	preload: string;
	naturalWidth: number;
	naturalHeight: number;
	videoWidth: number;
	videoHeight: number;
	duration: number;
	src: string;
}

let created: FakeMedia[] = [];
let revoked: string[] = [];
let nextUrl = 0;

function fakeMedia(): FakeMedia {
	const media: FakeMedia = {
		onload: null,
		onerror: null,
		onloadedmetadata: null,
		preload: '',
		naturalWidth: 0,
		naturalHeight: 0,
		videoWidth: 0,
		videoHeight: 0,
		duration: NaN,
		src: ''
	};
	created.push(media);
	return media;
}

const realCreateElement = document.createElement.bind(document);

beforeEach(() => {
	created = [];
	revoked = [];
	nextUrl = 0;
	vi.useFakeTimers();

	vi.stubGlobal('URL', {
		...URL,
		createObjectURL: vi.fn(() => `blob:probe-${nextUrl++}`),
		revokeObjectURL: vi.fn((url: string) => revoked.push(url))
	});
	vi.stubGlobal('Image', function FakeImage() {
		return fakeMedia();
	} as unknown as typeof Image);
	vi.spyOn(document, 'createElement').mockImplementation(((tag: string) => {
		if (tag === 'video' || tag === 'audio') return fakeMedia() as unknown as HTMLElement;
		return realCreateElement(tag);
	}) as typeof document.createElement);
});

afterEach(() => {
	vi.useRealTimers();
	vi.unstubAllGlobals();
	vi.restoreAllMocks();
});

function pngFile() {
	return new File([new Uint8Array([1, 2, 3])], 'shot.png', { type: 'image/png' });
}

describe('probeMediaFile', () => {
	it('reports an image size from the decoded header', async () => {
		const pending = probeMediaFile(pngFile(), 'image');
		created[0].naturalWidth = 1024;
		created[0].naturalHeight = 1536;
		created[0].onload?.();

		await expect(pending).resolves.toEqual({ width: 1024, height: 1536 });
	});

	it('reports size and duration for a video, asking only for metadata', async () => {
		const pending = probeMediaFile(new File([], 'clip.mp4', { type: 'video/mp4' }), 'video');
		expect(created[0].preload).toBe('metadata');

		created[0].videoWidth = 1280;
		created[0].videoHeight = 720;
		created[0].duration = 8.4;
		created[0].onloadedmetadata?.();

		await expect(pending).resolves.toEqual({ width: 1280, height: 720, durationSeconds: 8.4 });
	});

	it('reports only the duration for audio, which has no frame size', async () => {
		const pending = probeMediaFile(new File([], 'vo.wav', { type: 'audio/wav' }), 'audio');
		created[0].duration = 32.6;
		created[0].onloadedmetadata?.();

		await expect(pending).resolves.toEqual({
			width: undefined,
			height: undefined,
			durationSeconds: 32.6
		});
	});

	it('drops a duration the element reports as infinite rather than passing it on', async () => {
		const pending = probeMediaFile(new File([], 'stream.mp4', { type: 'video/mp4' }), 'video');
		created[0].videoWidth = 640;
		created[0].videoHeight = 480;
		created[0].duration = Infinity;
		created[0].onloadedmetadata?.();

		await expect(pending).resolves.toMatchObject({ durationSeconds: undefined });
	});

	it('RESOLVES EMPTY when the browser cannot decode it, never rejects', async () => {
		// A codec this browser lacks must not block an upload the backend accepts.
		const pending = probeMediaFile(pngFile(), 'image');
		created[0].onerror?.();

		await expect(pending).resolves.toEqual({});
	});

	it('gives up after the timeout instead of hanging the picker', async () => {
		const pending = probeMediaFile(pngFile(), 'image');
		vi.advanceTimersByTime(4000);

		await expect(pending).resolves.toEqual({});
	});

	it('settles once - a load arriving after the timeout cannot re-resolve', async () => {
		const pending = probeMediaFile(pngFile(), 'image');
		vi.advanceTimersByTime(4000);
		created[0].naturalWidth = 999;
		created[0].onload?.();

		await expect(pending).resolves.toEqual({});
		expect(revoked).toHaveLength(1);
	});

	it('revokes the object URL on the success path', async () => {
		const pending = probeMediaFile(pngFile(), 'image');
		created[0].onload?.();
		await pending;

		expect(revoked).toEqual(['blob:probe-0']);
	});

	it('revokes the object URL on the error path', async () => {
		const pending = probeMediaFile(pngFile(), 'image');
		created[0].onerror?.();
		await pending;

		expect(revoked).toEqual(['blob:probe-0']);
	});

	it('revokes the object URL on the timeout path', async () => {
		const pending = probeMediaFile(pngFile(), 'image');
		vi.advanceTimersByTime(4000);
		await pending;

		expect(revoked).toEqual(['blob:probe-0']);
	});

	it('leaks nothing across a run of files of every kind', async () => {
		const runs = [
			probeMediaFile(pngFile(), 'image'),
			probeMediaFile(new File([], 'clip.mp4'), 'video'),
			probeMediaFile(new File([], 'vo.wav'), 'audio')
		];
		created[0].onload?.();
		created[1].onerror?.();
		vi.advanceTimersByTime(4000);
		await Promise.all(runs);

		expect(revoked.sort()).toEqual(['blob:probe-0', 'blob:probe-1', 'blob:probe-2']);
	});

	it('mints no object URL at all for a file it will not probe', async () => {
		await expect(probeMediaFile(pngFile(), null)).resolves.toEqual({});
		expect(created).toHaveLength(0);
		expect(revoked).toHaveLength(0);
	});
});
