import { expect, test, vi } from 'vitest';
import { appendAssets, filesFromClipboard, kindOfFile, nameClipboardFiles, uploadIntoFilesStep, uploadSummary, type UploadItem, type UploadResponse } from './filesUpload';
import type { MediaAsset } from './types';

function file(name: string, type = 'image/png') {
	return new File(['x'], name, { type });
}

function harness(upload: (file: File) => Promise<UploadResponse>, extra: Record<string, unknown> = {}) {
	const batches: MediaAsset[][] = [];
	let items: UploadItem[] = [];
	const options = {
		mediaType: 'image',
		upload,
		blocked: () => null,
		onchange: (next: UploadItem[]) => (items = next),
		onassets: (assets: MediaAsset[]) => batches.push(assets),
		...extra
	};
	return { options, batches, items: () => items };
}

const ok = (id: string): UploadResponse => ({ success: true, data: { id, width: 640, height: 480 } });

test('uploaded files are added as library assets in the order they were dropped', async () => {
	const gates: Record<string, () => void> = {};
	const upload = (f: File) =>
		new Promise<UploadResponse>((resolve) => {
			gates[f.name] = () => resolve(ok(`id-${f.name}`));
		});
	const { options, batches } = harness(upload);

	const running = uploadIntoFilesStep([file('a.png'), file('b.png'), file('c.png')], options);
	await Promise.resolve();
	gates['c.png']();
	gates['b.png']();
	await new Promise((resolve) => setTimeout(resolve, 0));
	expect(batches).toEqual([]);
	gates['a.png']();
	const { assets, failed } = await running;

	expect(assets.map((asset) => asset.item_id)).toEqual(['id-a.png', 'id-b.png', 'id-c.png']);
	expect(batches.flat().map((asset) => asset.item_id)).toEqual(['id-a.png', 'id-b.png', 'id-c.png']);
	expect(failed).toBe(0);
	expect(assets[0]).toMatchObject({ kind: 'library', media_type: 'image', original_filename: 'a.png', width: 640, height: 480 });
});

test('a file of the wrong type is rejected on its own while the others upload', async () => {
	const upload = vi.fn(async (f: File) => ok(f.name));
	const { options, items } = harness(upload);

	const { assets, failed } = await uploadIntoFilesStep([file('a.png'), file('clip.mp4', 'video/mp4'), file('notes.txt', 'text/plain'), file('b.png')], options);

	expect(assets.map((asset) => asset.item_id)).toEqual(['a.png', 'b.png']);
	expect(failed).toBe(2);
	expect(upload).toHaveBeenCalledTimes(2);
	expect(items()[1]).toMatchObject({ status: 'error', error: 'This step holds images; this file is a video.' });
	expect(items()[2].error).toBe('Only images and videos can be added to a files step.');
});

test('an empty step takes the kind of the first file and refuses the other kind', async () => {
	const { options, items } = harness(async (f) => ok(f.name), { mediaType: null });

	const { assets } = await uploadIntoFilesStep([file('clip.mp4', 'video/mp4'), file('a.png')], options);

	expect(assets.map((asset) => asset.media_type)).toEqual(['video']);
	expect(items()[1].status).toBe('error');
});

test('a file over the plan limit is refused without being sent', async () => {
	const upload = vi.fn(async () => ok('x'));
	const { options, items } = harness(upload, { blocked: (f: File) => (f.name === 'big.png' ? 'This file is 90 MB; your plan allows files up to 25 MB.' : null) });

	await uploadIntoFilesStep([file('big.png'), file('small.png')], options);

	expect(upload).toHaveBeenCalledTimes(1);
	expect(items()[0].error).toContain('your plan allows');
});

test('a failed upload is reported for that file and the rest continue', async () => {
	const upload = async (f: File): Promise<UploadResponse> => {
		if (f.name === 'bad.png') throw new Error('boom');
		return ok(f.name);
	};
	const { options, items } = harness(upload);

	const { assets, failed } = await uploadIntoFilesStep([file('bad.png'), file('good.png')], options);

	expect(assets.map((asset) => asset.item_id)).toEqual(['good.png']);
	expect(failed).toBe(1);
	expect(items()[0].status).toBe('error');
	expect(uploadSummary(items())).toMatchObject({ total: 2, done: 1, active: false });
});

test('an upload response without a library id is an error, not a silent skip', async () => {
	const { options, items } = harness(async () => ({ success: true, data: {} }));

	const { assets } = await uploadIntoFilesStep([file('a.png')], options);

	expect(assets).toEqual([]);
	expect(items()[0].error).toBe('The upload did not return a library item.');
});

test('no more uploads run at once than the concurrency allows', async () => {
	let running = 0;
	let peak = 0;
	const upload = async (f: File) => {
		running += 1;
		peak = Math.max(peak, running);
		await new Promise((resolve) => setTimeout(resolve, 1));
		running -= 1;
		return ok(f.name);
	};
	const { options } = harness(upload, { concurrency: 2 });

	await uploadIntoFilesStep(Array.from({ length: 9 }, (_, index) => file(`f${index}.png`)), options);

	expect(peak).toBe(2);
});

test('appending skips files the step already holds', () => {
	const held: MediaAsset[] = [{ kind: 'library', item_id: 'a' }];

	const next = appendAssets(held, [{ kind: 'library', item_id: 'a' }, { kind: 'library', item_id: 'b' }, { kind: 'library', item_id: 'b' }]);

	expect(next.map((asset) => asset.item_id)).toEqual(['a', 'b']);
});

test('pasted clipboard files are collected and a screenshot gets a real name', () => {
	const shot = new File(['x'], 'image.png', { type: 'image/png' });
	const files = filesFromClipboard({ files: [shot] as unknown as FileList, items: [] as unknown as DataTransferItemList });

	expect(files).toEqual([shot]);
	const [named] = nameClipboardFiles(files);
	expect(named.name).toMatch(/^pasted-\d+-1\.png$/);
	expect(nameClipboardFiles([file('photo.jpg', 'image/jpeg')])[0].name).toBe('photo.jpg');
	expect(filesFromClipboard(null)).toEqual([]);
	expect(kindOfFile({ type: 'video/webm', name: 'a' })).toBe('video');
});
