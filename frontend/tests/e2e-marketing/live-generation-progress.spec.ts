import { test, expect, type WebSocketRoute } from '@playwright/test';
import { readFileSync } from 'node:fs';
import { join } from 'node:path';
import { loginAsOwner, choosePreset, beat } from './helpers';

// Scene 2 (★): a generation actually running — progress bar advancing, the
// live preview refining, the gallery strip appearing at the end. No GPU is
// available in this capture environment, so the "Generate" click never
// reaches the real (GPU-less) orchestrator: `POST /api/generations/start` is
// intercepted and answered directly, and `/ws/generation` is fully replayed
// via `page.routeWebSocket()` instead of connecting to the real backend.
//
// Every message on the wire below matches the REAL shapes, not an invented
// schema: `connection_established`/`subscribed` come straight from
// WebSocketHandler.handle_websocket (src/features/generation/websocket_handler.py);
// `generation_status` is GenerationOutputSerializer.serialize_output +
// serialize_progress_output (src/features/generation/handlers/artifact_handlers.py) -
// `progress` is a 0..1 fraction, `current_step`/`message` carry the same
// `<<PIPE:name>>`/`<<MODEL:name>>`/`<<PROGRESS:i/n>>` template markers real
// pipes emit (PipelineExecutor.hijack_pipe_generation_output auto-stamps
// `<<PIPE:{pipe.name}>>` as the title; `<<PROGRESS:i/n>>` matches
// tiled_detailer/sdxl's own literal usage; "generator" is the SDXL generator
// pipe's real `name`); `workbench_update`/`gallery_update` are
// serialize_image_output/serialize_gallery_output - the preview/gallery
// images below are the real bytes of the seeded SDXL potion.png example
// asset, not synthetic pixels; `generation_complete` matches
// GenerationController._handle_generation_output's completion branch
// (`{type: 'generation_complete', data: status.model_dump()}`).
test('live-generation-progress', async ({ page }) => {
	const generationId = '01MKTLIVEGENPROGRESS00001';
	const model = 'cyberrealisticPony_v180Coreshift.safetensors';
	const totalSteps = 30;
	const seed = 74213;
	const resolution: [number, number] = [1024, 1536];

	const potionPath = join(process.cwd(), '..', 'content', 'presets', 'marketplace', 'SDXL', 'public', 'examples', 'potion.png');
	const potionDataUri = `data:image/png;base64,${readFileSync(potionPath).toString('base64')}`;

	async function sleep(ms: number): Promise<void> {
		await new Promise((resolve) => setTimeout(resolve, ms));
	}

	function statusMsg(pipeId: number, pipeName: string, currentStep: string, progress: number, stepNum?: number) {
		return {
			type: 'generation_status',
			generation_id: generationId,
			pipe_id: pipeId,
			pipe_name: pipeName,
			output_type: 'progress',
			status: 'running',
			current_step: currentStep,
			message: `<<PIPE:${pipeName}>>`,
			progress,
			...(stepNum !== undefined ? { current_step_num: stepNum, total_steps: totalSteps } : {})
		};
	}

	function workbenchMsg(stepNum: number) {
		return {
			type: 'workbench_update',
			generation_id: generationId,
			pipe_id: 1,
			pipe_name: 'generator',
			output_type: 'image',
			temporary: true,
			seed,
			resolution,
			sampler: 'DPMPP_2M',
			cfg: 6.5,
			step: stepNum,
			image: potionDataUri
		};
	}

	// Drives the whole choreography once the client subscribes - runs
	// entirely inside this Node-side WS mock, so its pacing (not the
	// browser's) sets the recorded clip's rhythm.
	async function runSequence(ws: WebSocketRoute) {
		await sleep(400);
		ws.send(JSON.stringify(statusMsg(0, 'model_loader', `Loading SDXL checkpoint <<MODEL:${model}>>`, 0.0)));
		await sleep(1000);
		ws.send(JSON.stringify(statusMsg(1, 'generator', 'Sampling <<PROGRESS:3/30>>', 3 / totalSteps, 3)));
		await sleep(600);
		ws.send(JSON.stringify(workbenchMsg(3)));
		await sleep(600);
		ws.send(JSON.stringify(statusMsg(1, 'generator', 'Sampling <<PROGRESS:9/30>>', 9 / totalSteps, 9)));
		await sleep(600);
		ws.send(JSON.stringify(statusMsg(1, 'generator', 'Sampling <<PROGRESS:15/30>>', 15 / totalSteps, 15)));
		await sleep(500);
		ws.send(JSON.stringify(workbenchMsg(15)));
		await sleep(600);
		ws.send(JSON.stringify(statusMsg(1, 'generator', 'Sampling <<PROGRESS:21/30>>', 21 / totalSteps, 21)));
		await sleep(600);
		ws.send(JSON.stringify(statusMsg(1, 'generator', 'Sampling <<PROGRESS:27/30>>', 27 / totalSteps, 27)));
		await sleep(500);
		ws.send(JSON.stringify(workbenchMsg(27)));
		await sleep(500);
		ws.send(JSON.stringify(statusMsg(1, 'generator', 'Sampling <<PROGRESS:30/30>>', 1.0, 30)));
		await sleep(500);
		ws.send(
			JSON.stringify({
				type: 'gallery_update',
				generation_id: generationId,
				pipe_id: 2,
				pipe_name: 'gallery',
				output_type: 'gallery',
				images: [potionDataUri, potionDataUri],
				image_urls_list: [
					{ derived: false, seed, sampler: 'DPMPP_2M', cfg: 6.5, resolution: `${resolution[0]}x${resolution[1]}`, step: totalSteps },
					{ derived: false, seed: seed + 1, sampler: 'DPMPP_2M', cfg: 6.5, resolution: `${resolution[0]}x${resolution[1]}`, step: totalSteps }
				]
			})
		);
		await sleep(400);
		ws.send(
			JSON.stringify({
				type: 'generation_complete',
				data: {
					id: generationId,
					generation_id: generationId,
					status: 'completed',
					progress: 1.0,
					created_at: new Date().toISOString(),
					completed_at: new Date().toISOString()
				}
			})
		);
	}

	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		ws.send(JSON.stringify({ type: 'connection_established', client_id: 'e2e-marketing-mock' }));
		ws.onMessage((raw) => {
			const msg = JSON.parse(raw as string);
			if (msg.type === 'subscribe_generation') {
				ws.send(JSON.stringify({ type: 'subscribed', generation_id: msg.generation_id }));
				void runSequence(ws);
			}
		});
	});

	await page.route('**/api/generations/start', async (route) => {
		await route.fulfill({
			json: {
				success: true,
				data: {
					generation_id: generationId,
					status: {
						id: generationId,
						generation_id: generationId,
						status: 'pending',
						progress: 0.0,
						created_at: new Date().toISOString()
					},
					queue_position: null
				}
			}
		});
	});

	await loginAsOwner(page);
	await page.goto('/generate');
	await page.waitForLoadState('networkidle');
	await choosePreset(page, 'SDXL');
	await beat(page, 800);

	// The Generate button's accessible name is only "Generate" once
	// canGenerate is true (preset selected AND a prompt present) - otherwise
	// its aria-label is the disabled reason (e.g. "Enter a prompt to
	// generate"), so getByRole('button', { name: 'Generate' }) matches
	// nothing until the prompt is filled in.
	const promptEditor = page.locator('.inline-chip-editor[role="textbox"]').first();
	await expect(promptEditor).toBeVisible({ timeout: 15000 });
	await promptEditor.click();
	await page.keyboard.type('a glowing potion bottle on a wooden alchemist bench, cinematic lighting');
	await beat(page, 500);

	const generateButton = page.getByRole('button', { name: 'Generate', exact: true });
	await expect(generateButton).toBeEnabled({ timeout: 15000 });
	await generateButton.click();

	const progressBar = page.getByRole('progressbar', { name: 'Generation progress' });
	await expect(progressBar).toBeVisible({ timeout: 10000 });
	await expect(progressBar).toHaveAttribute('aria-valuenow', '10', { timeout: 10000 });
	await beat(page, 600);

	const preview = page.locator('img[src^="data:image/png;base64"]').first();
	await expect(preview).toBeVisible({ timeout: 10000 });
	await beat(page, 1500);

	await expect(progressBar).toHaveAttribute('aria-valuenow', '70', { timeout: 10000 });
	await beat(page, 1500);

	await expect(progressBar).toHaveAttribute('aria-valuenow', '100', { timeout: 10000 });
	await beat(page, 500);

	const galleryThumbnail = page.getByAltText(/Generated image/).first();
	await expect(galleryThumbnail).toBeVisible({ timeout: 10000 });
	await beat(page, 1500);
});
