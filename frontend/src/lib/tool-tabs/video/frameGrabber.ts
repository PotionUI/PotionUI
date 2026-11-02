import { seekVideo } from '$lib/media/editors/seekVideo';

export interface FrameGrabber {
	grab(time: number, signal: AbortSignal): Promise<string>;
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

export function createFrameGrabber(url: string, height = 48): FrameGrabber {
	const video = document.createElement('video');
	video.muted = true;
	video.playsInline = true;
	video.preload = 'auto';
	video.src = url;
	const canvas = document.createElement('canvas');
	return {
		async grab(time, signal) {
			await waitForData(video, signal);
			await seekVideo(video, time);
			if (signal.aborted) throw new Error('cancelled');
			const ratio = video.videoWidth > 0 && video.videoHeight > 0 ? video.videoWidth / video.videoHeight : 16 / 9;
			canvas.height = height;
			canvas.width = Math.max(1, Math.round(height * ratio));
			const context = canvas.getContext('2d');
			if (!context) throw new Error('No canvas');
			context.drawImage(video, 0, 0, canvas.width, canvas.height);
			return canvas.toDataURL('image/jpeg', 0.72);
		},
		dispose() {
			video.removeAttribute('src');
			video.load();
		}
	};
}
