import { test, expect, type Locator, type Page } from '@playwright/test';
import { screenshot } from './helpers';
import { resultImage, resultVideo } from './mediaToolMocks';
import {
	SIZES,
	audioItem,
	generatedImageItem,
	imageItem,
	mountField,
	pickTool,
	prepareFieldPage,
	published,
	videoItem,
	type PreparedLog,
	type Size
} from './mediaToolFieldHost';

const JOURNEY = 'media-tool-editors';
const size: Size = SIZES[0];

function editorOf(page: Page, name: string): Locator {
	return page.getByRole('dialog', { name, exact: true });
}

function lastRun(log: PreparedLog) {
	return log.runs.at(-1) as { steps: { tool: string; params: Record<string, unknown> }[]; inputs: unknown[]; output: { mode: string } };
}

test.describe('crop and frame', () => {
	test('sends one crop step for the chosen aspect, as a new item, and writes the result into the field', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');

		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await screenshot(page, JOURNEY, 'crop-square');
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log)).toEqual({
			steps: [{ tool: 'crop', params: { x: 128, y: 0, width: 512, height: 512 } }],
			inputs: [{ item_id: 'row-image-1' }],
			output: { mode: 'new' }
		});
		await expect(editor).toHaveCount(0);
		expect((await published(page)).at(-1)).toMatchObject({ url: '/api/e2e-media/image-9.png', type: 'image' });
	});

	test('sends rotate, flip and crop as the steps of one run', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');

		await editor.getByRole('button', { name: 'Rotate right' }).click();
		await editor.getByRole('button', { name: 'Flip horizontal' }).click();
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		const run = lastRun(log);
		expect(run.steps.map((step) => step.tool)).toEqual(['rotate', 'flip', 'crop']);
		expect(run.steps[0].params).toEqual({ degrees: 90 });
		expect(run.steps[1].params).toEqual({ axis: 'horizontal' });
		expect(run.steps[2].params.width).toBe(run.steps[2].params.height);
	});

	test('offers no Replace choice in a field and says the field takes the result', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');

		await expect(editor.getByRole('button', { name: 'Replace original' })).toHaveCount(0);
		await expect(editor.getByRole('group', { name: 'Save as' })).toHaveCount(0);
		await expect(editor.locator('[data-tool-note]')).toHaveText(
			'The result replaces this image in the field. The library copy stays as it is.'
		);
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log).output).toEqual({ mode: 'new' });
		await expect(editor).toHaveCount(0);
		expect((await published(page)).at(-1)).toMatchObject({ url: '/api/e2e-media/image-9.png' });
	});

	test('edits a generated image straight from storage, with no copy into the library and no replace', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: generatedImageItem() });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');

		await expect(editor.locator('[data-tool-note]')).toHaveText('The result replaces this image in the field. The library copy stays as it is.');
		await expect(editor.getByRole('button', { name: 'Replace original' })).toHaveCount(0);
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log).inputs).toEqual([{ generation_file_id: 'file-1' }]);
		expect(lastRun(log).output).toEqual({ mode: 'new' });
		expect(log.libraryCopies).toBe(0);
	});

	test('refuses to send an untouched crop', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');

		await expect(editor.getByText('Nothing to apply yet.')).toBeVisible();
		await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toBeDisabled();
		expect(log.runs).toEqual([]);
	});

	test('crops a video as well, from its own frame', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		test.skip(!log.hasVideo, 'this browser cannot record a video fixture');
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');

		await expect(editor.locator('video')).toHaveCount(1);
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		const [step] = lastRun(log).steps;
		expect(step.tool).toBe('crop');
		expect(step.params.width).toBe(step.params.height);
	});
});

test.describe('trim', () => {
	test('sends one trim step with start and end in seconds', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		test.skip(!log.hasVideo, 'this browser cannot record a video fixture');
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'trim');
		const editor = editorOf(page, 'Trim in / out');

		await editor.getByRole('button', { name: 'Trim out point' }).press('ArrowLeft');
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		const run = lastRun(log);
		expect(run.inputs).toEqual([{ item_id: 'row-video-1' }]);
		expect(run.output).toEqual({ mode: 'new' });
		expect(run.steps).toHaveLength(1);
		expect(run.steps[0].tool).toBe('trim');
		expect(run.steps[0].params.start).toBe(0);
		expect(run.steps[0].params.end as number).toBeGreaterThan(0);
		expect(run.steps[0].params.end as number).toBeLessThan(12);
	});

	test('trims audio on its waveform with the same step', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Voice', accept: 'audio/*' }, value: audioItem(1) });
		await pickTool(page, host, 'trim');
		const editor = editorOf(page, 'Trim on waveform');

		await editor.getByRole('button', { name: 'Trim in point' }).press('ArrowRight');
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log).steps).toEqual([{ tool: 'trim', params: { start: 0.1, end: 5 } }]);
	});

	test('refuses a selection that is still the whole clip', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Voice', accept: 'audio/*' }, value: audioItem(1) });
		await pickTool(page, host, 'trim');
		const editor = editorOf(page, 'Trim on waveform');

		await expect(editor.getByText('Move a handle to choose what to keep.')).toBeVisible();
		await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toBeDisabled();
		expect(log.runs).toEqual([]);
	});
});

test.describe('resize', () => {
	test('sends the chosen size as one resize step, without a quality for an image', async ({ page }) => {
		const log = await prepareFieldPage(page, size);
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'resize');
		const editor = editorOf(page, 'Resize');

		await editor.getByRole('button', { name: /^50%/ }).click();
		await expect(editor.locator('[data-testid="resize-result"]')).toHaveText('384×256');
		await screenshot(page, JOURNEY, 'resize-half');
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log).steps).toEqual([{ tool: 'resize', params: { width: 384, height: 256 } }]);
	});
});

for (const viewport of SIZES) {
	test.describe(`crop and resize modals at ${viewport.tag}`, () => {
		test('the crop modal fits the screen with its aspect, orientation and size controls', async ({ page }) => {
			await page.setViewportSize({ width: viewport.width, height: viewport.height });
			await prepareFieldPage(page, viewport);
			const host = await mountField(page, viewport, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
			await pickTool(page, host, 'crop');
			const editor = editorOf(page, 'Crop & frame');
			await expect(editor.getByRole('button', { name: '3:4', exact: true })).toBeVisible();
			await editor.getByRole('button', { name: '1:1', exact: true }).click();
			await screenshot(page, JOURNEY, `crop-modal-${viewport.tag}`);
			await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toBeVisible();
		});

		test('the resize modal fits the screen with its sizes and custom fields', async ({ page }) => {
			await page.setViewportSize({ width: viewport.width, height: viewport.height });
			await prepareFieldPage(page, viewport);
			const host = await mountField(page, viewport, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
			await pickTool(page, host, 'resize');
			const editor = editorOf(page, 'Resize');
			await editor.getByRole('button', { name: /^50%/ }).click();
			await expect(editor.locator('[data-testid="resize-result"]')).toHaveText('384×256');
			await screenshot(page, JOURNEY, `resize-modal-${viewport.tag}`);
			await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toBeVisible();
		});
	});
}

test.describe('frame', () => {
	test('saves one frame as an image and never offers a replace', async ({ page }) => {
		const log = await prepareFieldPage(page, size, { items: [resultImage({ id: 'still-1', url: '/api/e2e-media/image-8.png' })] });
		test.skip(!log.hasVideo, 'this browser cannot record a video fixture');
		const host = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await pickTool(page, host, 'frame');
		const editor = editorOf(page, 'Extract a frame');

		await expect(editor.getByRole('button', { name: 'Replace original' })).toHaveCount(0);
		await editor.getByRole('button', { name: 'Save frame', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log)).toEqual({
			steps: [{ tool: 'frame', params: { time: 0 } }],
			inputs: [{ item_id: 'row-video-1' }],
			output: { mode: 'new' }
		});
		await expect.poll(async () => (await published(page)).at(-1)).toMatchObject({ url: '/api/e2e-media/image-8.png', type: 'image' });
	});
});

test.describe('split', () => {
	test('sends the part length in seconds and refuses one that does not split anything', async ({ page }) => {
		const log = await prepareFieldPage(page, size, {
			items: [0, 1, 2].map((n) => ({
				id: `part-${n}`,
				filename: `part-${n}.wav`,
				media_type: 'audio',
				url: `/api/e2e-media/audio-${n + 5}.wav`,
				duration_seconds: 2
			}))
		});
		const host = await mountField(page, size, { config: { title: 'Voice', accept: 'audio/*' }, value: audioItem(1) });
		await pickTool(page, host, 'split');
		const editor = editorOf(page, 'Split into parts');

		await expect(editor.getByRole('button', { name: 'Split', exact: true })).toBeDisabled();
		await expect(editor.getByText(/nothing to split/)).toBeVisible();

		await editor.locator('#split-part-seconds').fill('2');
		await expect(editor.getByText(/3 parts/)).toBeVisible();
		await editor.getByRole('button', { name: 'Split', exact: true }).click();

		await expect.poll(() => log.runs.length).toBe(1);
		expect(lastRun(log)).toEqual({
			steps: [{ tool: 'split', params: { part_seconds: 2 } }],
			inputs: [{ item_id: 'row-audio-1' }],
			output: { mode: 'new' }
		});
		await expect(editor).toHaveCount(0);
	});
});

test.describe('a run in flight', () => {
	test('shows progress in the footer, cancels from it, and keeps the editor open', async ({ page }) => {
		const log = await prepareFieldPage(page, size, { hold: true });
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect(editor.locator('[data-tool-progress]')).toContainText('40%');
		await screenshot(page, JOURNEY, 'crop-running');
		await editor.getByRole('button', { name: 'Cancel edit' }).click();

		await expect.poll(() => log.cancels).toEqual(['run-1']);
		await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toBeEnabled();
		expect(await published(page)).toEqual([]);
	});

	test('finishes by itself when the server completes the run', async ({ page }) => {
		const log = await prepareFieldPage(page, size, { hold: true, items: [resultVideo()] });
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();
		await expect(editor.locator('[data-tool-progress]')).toBeVisible();

		log.release();

		await expect(editor).toHaveCount(0);
		expect((await published(page)).at(-1)).toMatchObject({ url: '/api/e2e-media/video-9.webm' });
	});
});

test.describe('refusals', () => {
	test('shows the plain storage message for a quota refusal', async ({ page }) => {
		await prepareFieldPage(page, size, {
			refuse: {
				status: 403,
				body: {
					error: 'limit_exceeded',
					code: 'storage_quota_exceeded',
					kind: 'storage_bytes',
					label: 'Storage',
					format: 'bytes',
					message: 'Your storage is full. You have used 20.0 of 20 GB.'
				}
			}
		});
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect(editor.getByText('Storage is full. Free up space in History or Library, then try again.')).toBeVisible();
		await expect(editor.getByText(/status code/)).toHaveCount(0);
		await expect(editor.getByRole('button', { name: 'Apply', exact: true })).toBeEnabled();
	});

	test('shows the server reason for a rejected crop', async ({ page }) => {
		await prepareFieldPage(page, size, {
			refuse: { status: 400, body: { error: 'invalid_params', message: 'The crop rectangle does not fit inside 768x512', field: 'width', step_index: 0 } }
		});
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await pickTool(page, host, 'crop');
		const editor = editorOf(page, 'Crop & frame');
		await editor.getByRole('button', { name: '1:1', exact: true }).click();
		await editor.getByRole('button', { name: 'Apply', exact: true }).click();

		await expect(editor.getByText('The crop rectangle does not fit inside 768x512')).toBeVisible();
	});
});

test.describe('the field toolbar', () => {
	test('lists the catalog tools for an image, and Crop to subject greyed with its reason for a jpeg', async ({ page }) => {
		await prepareFieldPage(page, size);
		const host = await mountField(page, size, {
			config: { title: 'Image', accept: 'image/*' },
			value: imageItem(1, { name: 'photo.jpg', url: '/api/e2e-media/image-1.png' })
		});

		await host.locator('[data-tools-trigger]').first().click();
		const menu = page.locator('[data-tools-menu]');
		for (const id of ['edit', 'crop', 'resize', 'rotate', 'flip', 'fit_canvas', 'color_key', 'full']) {
			await expect(menu.locator(`[data-tool="${id}"]`)).toBeVisible();
		}
		await expect(menu.locator('[data-tool="trim"]')).toHaveCount(0);
		await expect(menu.locator('[data-tool="concat"]')).toHaveCount(0);
		await expect(menu.locator('[data-tool="crop_subject"]')).toHaveAttribute('aria-disabled', 'true');
		await expect(menu).toContainText('Needs a transparent image');
		await screenshot(page, JOURNEY, 'field-tools-image');
	});

	test('lists the video and audio tools for each kind', async ({ page }) => {
		await prepareFieldPage(page, size);
		const video = await mountField(page, size, { config: { title: 'Clip', accept: 'video/*' }, value: videoItem(1) });
		await video.locator('[data-tools-trigger]').first().click();
		const menu = page.locator('[data-tools-menu]');
		for (const id of ['trim', 'crop', 'resize', 'rotate', 'flip', 'speed', 'frame']) {
			await expect(menu.locator(`[data-tool="${id}"]`)).toBeVisible();
		}
		await expect(menu.locator('[data-tool="fit_canvas"]')).toHaveCount(0);
		await page.keyboard.press('Escape');

		const audio = await mountField(page, size, { config: { title: 'Voice', accept: 'audio/*' }, value: audioItem(1) });
		await audio.locator('[data-tools-trigger]').first().click();
		for (const id of ['trim', 'speed', 'split']) {
			await expect(menu.locator(`[data-tool="${id}"]`)).toBeVisible();
		}
		await expect(menu.locator('[data-tool="crop"]')).toHaveCount(0);
	});

	test('offers only the painter until the catalog arrives, then the rest without a reload', async ({ page }) => {
		let open: () => void = () => undefined;
		const catalogGate = new Promise<void>((resolve) => (open = resolve));
		await prepareFieldPage(page, size, { catalogGate });
		const host = await mountField(page, size, { config: { title: 'Image', accept: 'image/*' }, value: imageItem(1) });
		await host.locator('[data-tools-trigger]').first().click();
		const menu = page.locator('[data-tools-menu]');
		await expect(menu.locator('[data-tool="edit"]')).toBeVisible();
		await expect(menu.locator('[data-tool="rotate"]')).toHaveCount(0);

		open();
		await expect(menu.locator('[data-tool="rotate"]')).toBeVisible();
	});
});
