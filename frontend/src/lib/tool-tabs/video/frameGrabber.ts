import { seekVideo } from '$lib/media/seekVideo';

export interface FrameGrabber {
	grab(time: number, signal: AbortSignal, size?: GrabSize): Promise<string>;
	dispose(): void;
}

function waitForData(video: HTMLVideoElement, signal: AbortSignal): Promise<void> {
	if (video.readyState >= 1) return Promise.resolve();
	return new Promise((resolve, reject) => {
		const done = () => {
			cleanup();
			resolve();
		};
		const failed = () => {
			cleanup();
			reject(new Error('The clip could not be read'));
		};
		const cleanup = () => {
			video.removeEventListener('loadedmetadata', done);
			video.removeEventListener('error', failed);
			signal.removeEventListener('abort', failed);
		};
		video.addEventListener('loadedmetadata', done);
		video.addEventListener('error', failed);
		signal.addEventListener('abort', failed);
	});
}

export interface GrabSize {
	width: number;
	height: number;
	quality?: number;
}

function encodeJpeg(canvas: HTMLCanvasElement, quality: number): Promise<string> {
	return new Promise((resolve, reject) => {
		canvas.toBlob(
			(blob) => {
				if (!blob) {
					reject(new Error('Could not encode the frame'));
					return;
				}
				const reader = new FileReader();
				reader.onload = () => resolve(String(reader.result));
				reader.onerror = () => reject(new Error('Could not encode the frame'));
				reader.readAsDataURL(blob);
			},
			'image/jpeg',
			quality
		);
	});
}

export function createFrameGrabber(url: string, height = 48, defaultSize?: GrabSize): FrameGrabber {
	const video = document.createElement('video');
	video.muted = true;
	video.playsInline = true;
	video.preload = 'auto';
	video.src = url;
	const canvas = document.createElement('canvas');
	let tail: Promise<unknown> = Promise.resolve();

	async function run(time: number, signal: AbortSignal, size: GrabSize | undefined): Promise<string> {
		if (signal.aborted) throw new Error('cancelled');
		await waitForData(video, signal);
		await seekVideo(video, time);
		if (signal.aborted) throw new Error('cancelled');
		const ratio = video.videoWidth > 0 && video.videoHeight > 0 ? video.videoWidth / video.videoHeight : 16 / 9;
		canvas.height = size ? size.height : height;
		canvas.width = size ? size.width : Math.max(1, Math.round(height * ratio));
		const context = canvas.getContext('2d');
		if (!context) throw new Error('No canvas');
		context.imageSmoothingQuality = 'high';
		context.drawImage(video, 0, 0, canvas.width, canvas.height);
		return encodeJpeg(canvas, size?.quality ?? 0.72);
	}

	return {
		grab(time, signal, size = defaultSize) {
			const result = tail.then(() => run(time, signal, size));
			tail = result.catch(() => undefined);
			return result;
		},
		dispose() {
			video.removeAttribute('src');
			video.load();
		}
	};
}

interface Pooled {
	grabber: FrameGrabber;
	refs: number;
}

const pool = new Map<string, Pooled>();

export function acquireFrameGrabber(url: string): FrameGrabber {
	let entry = pool.get(url);
	if (!entry) {
		entry = { grabber: createFrameGrabber(url, 160), refs: 0 };
		pool.set(url, entry);
	}
	entry.refs += 1;
	const held = entry;
	let released = false;
	return {
		grab: (time, signal, size) => held.grabber.grab(time, signal, size),
		dispose() {
			if (released) return;
			released = true;
			held.refs -= 1;
			if (held.refs > 0) return;
			if (pool.get(url) === held) pool.delete(url);
			held.grabber.dispose();
		}
	};
}
