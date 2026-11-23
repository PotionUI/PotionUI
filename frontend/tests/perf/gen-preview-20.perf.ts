import { expect, test, type WebSocketRoute } from '@playwright/test';
import { generationStatusMessage } from '../e2e/generationMocks';
import { PREVIEW_COUNT, installExpandedForm, previewImages } from './fixtures';
import { discoverPreset, percentile, runScenario, seedGenerationTabs, type PresetHandle } from './perfKit';

const GENERATION_ID = '01PERFGENPREVIEW00000001';
const INTERVAL_MS = 100;
const TOTAL_STEPS = PREVIEW_COUNT;
const SEED = 74213;
const EDITOR = '.inline-chip-editor[role="textbox"]';

interface Observed {
	length: number;
	middle: string;
	start: number;
	done: number;
}

const MIN_RECEIVED = Math.ceil(TOTAL_STEPS * 0.5);

const keyOf = (length: number, middle: string) => `${length}:${middle}`;

function installPreviewObserver() {
	const w = window as any;
	const seen = new WeakMap<object, string>();
	w.__previews = [] as Observed[];
	const asDataUrl = async (src: string): Promise<string> => {
		if (src.startsWith('data:image/')) return src;
		const blob = await (await fetch(src)).blob();
		return new Promise<string>((resolve, reject) => {
			const reader = new FileReader();
			reader.onload = () => resolve(String(reader.result));
			reader.onerror = () => reject(reader.error);
			reader.readAsDataURL(blob);
		});
	};
	const handle = (node: Element) => {
		if (!(node instanceof HTMLImageElement) || node.getAttribute('alt') !== 'Current generation') return;
		const src = node.getAttribute('src') ?? '';
		if (!(src.startsWith('data:image/') || src.startsWith('blob:')) || seen.get(node) === src) return;
		seen.set(node, src);
		const start = performance.timeOrigin + performance.now();
		const identity = asDataUrl(src).then((url) => ({ length: url.length, middle: url.slice(300, 360) }));
		const painted = node
			.decode()
			.catch(() => undefined)
			.then(() => new Promise<number>((resolve) => requestAnimationFrame(() => resolve(performance.timeOrigin + performance.now()))));
		Promise.all([identity, painted])
			.then(([{ length, middle }, done]) => w.__previews.push({ length, middle, start, done }))
			.catch(() => undefined);
	};
	new MutationObserver((records) => {
		for (const record of records) {
			if (record.type === 'attributes') handle(record.target as Element);
			record.addedNodes.forEach((added) => {
				if (added instanceof Element) {
					handle(added);
					added.querySelectorAll('img').forEach(handle);
				}
			});
		}
	}).observe(document, { subtree: true, childList: true, attributes: true, attributeFilter: ['src'] });
}

test('gen-preview-20', async ({ browser }) => {
	const images = previewImages();
	await runScenario<PresetHandle>(browser, {
		name: 'gen-preview-20',
		setup: discoverPreset,
		configure: async (context, handle) => {
			await seedGenerationTabs(context, handle, [{ id: 'perf-preview-1', name: 'Perf', prompt: 'a lantern in a harbor' }]);
			await installExpandedForm(context, { fields: 0 });
		},
		prepare: async (kit) => {
			const sockets: WebSocketRoute[] = [];
			(kit as any).sockets = sockets;
			await kit.page.routeWebSocket(/\/ws\/generation/, (ws) => {
				sockets.push(ws);
				ws.send(JSON.stringify({ type: 'connection_established', client_id: 'perf-mock' }));
				ws.onMessage((raw) => {
					const message = JSON.parse(raw as string);
					if (message.type === 'subscribe_generation') ws.send(JSON.stringify({ type: 'subscribed', generation_id: message.generation_id }));
				});
			});
			await kit.page.route('**/api/generations/start', (route) =>
				route.fulfill({
					json: {
						success: true,
						data: {
							generation_id: GENERATION_ID,
							status: { id: GENERATION_ID, generation_id: GENERATION_ID, status: 'pending', progress: 0, created_at: new Date().toISOString() },
							queue_position: null
						}
					}
				})
			);
			await kit.page.goto('/generate');
			const editor = kit.page.locator(EDITOR).first();
			await expect(editor).toBeVisible({ timeout: 30000 });
			const generate = kit.page.getByRole('button', { name: 'Generate', exact: true });
			await expect(generate).toBeEnabled({ timeout: 30000 });
			await kit.page.waitForLoadState('networkidle');
			await generate.click();
			await expect.poll(() => sockets.length, { timeout: 15000 }).toBeGreaterThan(0);
			await kit.page.evaluate(installPreviewObserver);
		},
		measure: async (kit) => {
			const sockets = (kit as any).sockets as WebSocketRoute[];
			const ws = sockets[sockets.length - 1];
			const send = (message: object) => ws.send(JSON.stringify(message));
			const sendByKey = new Map<string, number>();
			const sends: number[] = [];
			const { metrics } = await kit.window(async () => {
				const begin = Date.now();
				for (let step = 1; step <= TOTAL_STEPS; step++) {
					const wait = begin + (step - 1) * INTERVAL_MS - Date.now();
					if (wait > 0) await new Promise((resolve) => setTimeout(resolve, wait));
					send(generationStatusMessage({ generationId: GENERATION_ID, pipeId: 1, pipeName: 'generator', currentStep: `Sampling <<PROGRESS:${step}/${TOTAL_STEPS}>>`, progress: step / TOTAL_STEPS, stepNum: step, totalSteps: TOTAL_STEPS }));
					const sentAt = Date.now();
					sends.push(sentAt);
					sendByKey.set(keyOf(images[step - 1].length, images[step - 1].slice(300, 360)), sentAt);
					send({
						type: 'workbench_update',
						generation_id: GENERATION_ID,
						pipe_id: 1,
						pipe_name: 'generator',
						output_type: 'image',
						temporary: true,
						seed: SEED,
						resolution: [1024, 1024],
						sampler: 'DPMPP_2M',
						cfg: 6.5,
						step,
						image: images[step - 1]
					});
				}
				await new Promise((resolve) => setTimeout(resolve, INTERVAL_MS));
				send({
					type: 'gallery_update',
					generation_id: GENERATION_ID,
					pipe_id: 2,
					pipe_name: 'gallery',
					output_type: 'gallery',
					images: [images[TOTAL_STEPS - 1]],
					image_urls_list: [{ derived: false, seed: SEED, sampler: 'DPMPP_2M', cfg: 6.5, resolution: '1024x1024', step: TOTAL_STEPS }]
				});
				send({
					type: 'generation_complete',
					data: { id: GENERATION_ID, generation_id: GENERATION_ID, status: 'completed', progress: 1, created_at: new Date().toISOString(), completed_at: new Date().toISOString() }
				});
				await kit.page.waitForTimeout(750);
			});
			const observed = (await kit.page.evaluate(() => (window as any).__previews)) as Observed[];
			const seenKeys = new Set<string>();
			const latencies: number[] = [];
			for (const item of observed) {
				const key = keyOf(item.length, item.middle);
				if (seenKeys.has(key) || item.start < sends[0]) continue;
				const sentAt = sendByKey.get(key);
				if (sentAt === undefined) continue;
				seenKeys.add(key);
				latencies.push(item.done - sentAt);
			}
			const received = latencies.length;
			if (received < MIN_RECEIVED || received > TOTAL_STEPS) {
				throw new Error(`${received} previews rendered for ${TOTAL_STEPS} sent; expected ${MIN_RECEIVED} to ${TOTAL_STEPS} after the app coalesces frames`);
			}
			return { ...metrics, previews_received: received, preview_ms_p50: percentile(latencies, 0.5), preview_ms_p95: percentile(latencies, 0.95) };
		}
	});
});
