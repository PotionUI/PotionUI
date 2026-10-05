// @vitest-environment jsdom
import { describe, it, expect, vi, beforeEach } from 'vitest';

class StubResizeObserver {
	observe() {}
	unobserve() {}
	disconnect() {}
}
vi.stubGlobal('ResizeObserver', StubResizeObserver);

const editMediaItem = vi.fn();
const extractMediaFrame = vi.fn();
const splitMediaItem = vi.fn();

vi.mock('$lib/services/api/index', () => ({
	api: {
		editMediaItem: (...args: unknown[]) => editMediaItem(...args),
		extractMediaFrame: (...args: unknown[]) => extractMediaFrame(...args),
		splitMediaItem: (...args: unknown[]) => splitMediaItem(...args),
		listUploads: async () => ({ success: true, data: { uploads: [], total: 0, limit: 100, offset: 0 } }),
		listGenerationMedia: async () => ({ success: false }),
		copyGenerationFileToLibrary: async () => ({ success: false }),
		uploadMedia: async () => ({ success: false }),
		deleteLibraryItem: async () => ({ success: true })
	}
}));

const { default: MediaEditors } = await import('$lib/media/editors/MediaEditors.svelte');
const { createClassComponent } = await import('svelte/legacy');
const { tick } = await import('svelte');

function mount(props: Record<string, unknown>) {
	const target = document.createElement('div');
	document.body.appendChild(target);
	createClassComponent({ component: MediaEditors as any, target, props });
	return document.body;
}

function buttonByText(target: HTMLElement, text: string): HTMLButtonElement | undefined {
	return Array.from(target.querySelectorAll('button')).find((b) => (b.textContent || '').trim() === text);
}

async function settle() {
	for (let i = 0; i < 5; i += 1) {
		await tick();
		await Promise.resolve();
	}
}

const FULL_MESSAGE = 'Storage is full. Free up space in History or Library, then try again.';

const storageRefusal = {
	message: 'Request failed with status code 403',
	response: {
		status: 403,
		data: {
			detail: {
				error: 'limit_exceeded',
				code: 'storage_quota_exceeded',
				kind: 'storage_bytes',
				label: 'Storage',
				format: 'bytes',
				message: 'Your storage is full. You have used 20.0 of 20 GB.'
			}
		}
	}
};

const image = { url: '/u/p.png', kind: 'image' as const, fileName: 'p.png', itemId: 'r1', storedPath: 'uploads/p.png', width: 1024, height: 1536 };
const clip = { url: '/u/c.mp4', kind: 'video' as const, fileName: 'c.mp4', itemId: 'r2', storedPath: 'uploads/c.mp4', width: 1280, height: 720, durationSeconds: 8.4, fps: 24 };
const audio = { url: '/u/t.mp3', kind: 'audio' as const, fileName: 't.mp3', itemId: 'r3', storedPath: 'uploads/t.mp3', durationSeconds: 65 };

beforeEach(() => {
	document.body.innerHTML = '';
	editMediaItem.mockReset().mockRejectedValue(storageRefusal);
	extractMediaFrame.mockReset().mockRejectedValue(storageRefusal);
	splitMediaItem.mockReset().mockRejectedValue(storageRefusal);
});

describe('editor saves refused for storage', () => {
	it('keeps the image editor open and shows the plain storage message', async () => {
		const onClose = vi.fn();
		const onResult = vi.fn();
		const target = mount({ request: { kind: 'crop', source: image, itemIndex: null }, onClose, onResult });
		await settle();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(target.textContent).toContain(FULL_MESSAGE);
		expect(target.textContent).not.toContain('status code');
		expect(onClose).not.toHaveBeenCalled();
		expect(onResult).not.toHaveBeenCalled();
		expect(buttonByText(target, 'Save as new')).toBeDefined();
	});

	it('shows it for a video frame grab', async () => {
		const onClose = vi.fn();
		const target = mount({ request: { kind: 'frame', source: clip, itemIndex: null }, onClose, onResult: vi.fn() });
		await settle();

		buttonByText(target, 'Save frame')!.click();
		await settle();

		expect(target.textContent).toContain(FULL_MESSAGE);
		expect(onClose).not.toHaveBeenCalled();
	});

	it('shows it for an audio split', async () => {
		const onClose = vi.fn();
		const target = mount({ request: { kind: 'split', source: audio, itemIndex: null }, onClose, onResult: vi.fn() });
		await settle();

		buttonByText(target, 'Split')!.click();
		await settle();

		expect(target.textContent).toContain(FULL_MESSAGE);
		expect(onClose).not.toHaveBeenCalled();
	});

	it('words a daily-style refusal with the countdown instead', async () => {
		editMediaItem.mockRejectedValue({
			response: {
				data: {
					detail: {
						error: 'limit_exceeded',
						kind: 'generations_per_day',
						label: 'Generations today',
						format: 'count',
						resets_at: new Date(Date.now() + 3 * 3600 * 1000 + 30_000).toISOString()
					}
				}
			}
		});
		const target = mount({ request: { kind: 'crop', source: image, itemIndex: null }, onClose: vi.fn(), onResult: vi.fn() });
		await settle();

		buttonByText(target, '1:1')!.click();
		await settle();
		buttonByText(target, 'Save as new')!.click();
		await settle();

		expect(target.textContent).toContain('Daily limit reached, resets in 3 h');
	});
});
