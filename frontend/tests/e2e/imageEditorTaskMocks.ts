import { expect, type Page, type Route, type WebSocketRoute } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { clearEdits, loginAsOwner } from './helpers';
import { chunk } from './mediaFieldHelpers';
import { mockMediaTools, solidPng } from './mediaToolMocks';

export type Json = Record<string, unknown>;

export const GENERATION_ID = 'gen-e2e-tasks';
export const FILE_ID = 604;
export const SOURCE_FILE = `outputs/2026-11-01/${GENERATION_ID}/harbor-dawn.png`;
export const EDIT_ID = 'ed_e2e_tasks';
export const runGeneration = (n: number) => `gen-e2e-task-run-${n}`;

const FORM_SCHEMA = {
	properties: {
		generation: {
			type: 'group',
			title: 'Generation',
			children: [
				{ name: 'steps', type: 'slider', title: 'Steps', default: 28 },
				{ name: 'seed', type: 'seed', title: 'Seed', default: -1 }
			]
		}
	}
};

export const qwen = (over: Json = {}): Json => ({ preset_id: 'qwen21', preset_name: 'Qwen-Image 2.1', mode: 'editor:edit', from_mode: 'edit', mode_label: 'Edit', label: null, short_description: null, order: 0, variant: null, region_strategy: 'mask', via: null, qualifies: true, reason: null, quantity_field: null, owned: ['source_image'], ...over });

export const matting = (over: Json = {}): Json => ({ preset_id: 'birefnet', preset_name: 'BiRefNet', mode: 'editor:matte', from_mode: null, mode_label: 'Find the subject', label: null, short_description: null, order: 0, variant: null, region_strategy: 'whole', via: null, qualifies: true, reason: null, quantity_field: null, owned: ['source_image'], ...over });

export function taskEntry(id: string, label: string, over: Json = {}): Json {
	const base: Record<string, Json> = {
		edit_region: { region: 'optional', prompt: 'required', fallback: null },
		remove_object: { region: 'required', prompt: 'none', grow: 0.04, fallback: { task: 'edit_region', prompt: 'Remove the marked object.' } },
		replace_background: { region: 'required', prompt: 'required', matte: true, fallback: { task: 'edit_region', prompt: 'Keep the subject exactly as it is. Replace everything marked with this background: {prompt}' } },
		find_subject: { region: 'none', prompt: 'none', internal: true, fallback: null }
	};
	return { id, label, icon: 'sparkles', offers: [qwen()], default: null, ...(base[id] ?? { region: 'optional', prompt: 'required', fallback: null }), ...over };
}

export function rgbaPng(width: number, height: number, alphaAt: (x: number, y: number) => number): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < height; y++) {
		const row = Buffer.alloc(1 + width * 4);
		for (let x = 0; x < width; x++) {
			row[1 + x * 4] = 200;
			row[2 + x * 4] = 120;
			row[3 + x * 4] = 80;
			row[4 + x * 4] = alphaAt(x, y);
		}
		rows.push(row);
	}
	const header = Buffer.alloc(13);
	header.writeUInt32BE(width, 0);
	header.writeUInt32BE(height, 4);
	header[8] = 8;
	header[9] = 6;
	return Buffer.concat([Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]), chunk('IHDR', header), chunk('IDAT', deflateSync(Buffer.concat(rows))), chunk('IEND', Buffer.alloc(0))]);
}

export function subjectPng(width = 640, height = 480): Buffer {
	const cx = width / 2;
	const cy = height / 2;
	return rgbaPng(width, height, (x, y) => {
		const d = Math.hypot((x - cx) / (width * 0.22), (y - cy) / (height * 0.34));
		if (d <= 0.9) return 255;
		if (d >= 1.1) return 0;
		return Math.round(((1.1 - d) / 0.2) * 255);
	});
}

export interface TaskServer {
	tasks: Json[];
	runs: Array<{ task: string; body: Json }>;
	immediate: Record<string, Json>;
	live: Json;
	runCount: number;
	mediaRuns: Json[];
	artifacts: number;
	sockets: WebSocketRoute[];
}

export function runBody(server: TaskServer, status: string, extra: Json = {}, n = server.runCount): Json {
	return {
		id: `er_${n}`,
		task: 'edit_region',
		status,
		edit_id: EDIT_ID,
		step_id: 's1',
		generation_id: runGeneration(n),
		progress: status === 'done' ? 1 : 0,
		error: null,
		soft: false,
		rect: { x: 0.55, y: 0.35, width: 0.3, height: 0.4 },
		candidates: [],
		...extra
	};
}

export function candidates(count: number): Json[] {
	return Array.from({ length: count }, (_, i) => ({ patch: { item_id: `patch-${i + 1}` }, url: `/api/e2e-art/patch-${i + 1}.png`, seed: 100 + i, generation_file_id: String(900 + i) }));
}

async function ok(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

export async function mockTasks(page: Page, tasks: Json[]): Promise<TaskServer> {
	const server: TaskServer = { tasks, runs: [], immediate: {}, live: {}, runCount: 0, mediaRuns: [], artifacts: 0, sockets: [] };
	let revision = 1;
	const source = solidPng(640, 480);
	const small = solidPng(64, 64);
	const subject = subjectPng();
	const saves: Json[] = [];
	const generation = () => {
		const now = new Date().toISOString();
		return { id: GENERATION_ID, preset_id: 'krea2', preset_name: 'Krea 2', mode: 'txt2img', form_data: {}, status: 'completed', progress: 1, created_at: now, updated_at: now, completed_at: now, files: [{ id: FILE_ID, file_path: SOURCE_FILE, file_type: 'image', is_final: true, created_at: now, width: 640, height: 480 }] };
	};
	await mockMediaTools(page);
	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		server.mediaRuns.push(route.request().postDataJSON() as Json);
		await ok(route, { run_id: `run-${server.mediaRuns.length}` }, 202);
	});
	await page.route(/\/api\/generations\/history(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, { generations: [generation()], total: 1 }) : route.fallback()));
	await page.route(`**/api/media/generations/${GENERATION_ID}/harbor-dawn.png*`, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: source }));
	await page.route('**/api/e2e-art/**', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route('**/api/e2e-art/subject.png*', (route) => route.fulfill({ status: 200, contentType: 'image/png', body: subject }));
	await page.route(/\/api\/media-tools\/artifacts$/, (route) => {
		server.artifacts += 1;
		return ok(route, { id: `art-${server.artifacts}`, media_type: 'image', mime_type: 'image/png', url: '/api/e2e-art/art.png', width: 480, height: 480, duration_seconds: null, fps: null, size: 2048 });
	});
	await page.route(/\/api\/media\/serve(\?.*)?$/, (route) => route.fulfill({ status: 200, contentType: 'image/png', body: small }));
	await page.route(/\/api\/media\/upload$/, (route) => ok(route, { path: 'uploads/mask.png', relative_path: 'uploads/mask.png', filename: 'mask.png', url: '/api/e2e-art/mask.png', width: 480, height: 480, size: 4096 }));
	await page.route(/\/api\/library\/items(\?.*)?$/, (route) =>
		route.request().method() === 'GET'
			? ok(route, { items: [{ id: 'lib-bg-1', filename: 'sunset.png', original_filename: 'Sunset beach.png', media_type: 'image', url: '/api/e2e-art/sunset.png', width: 480, height: 480, tags: [], created_at: new Date().toISOString() }], total: 1, limit: 60, offset: 0 })
			: route.fallback()
	);
	await page.route('**/api/presets/*/form*', (route) => ok(route, { preset_id: 'mock', form_schema: FORM_SCHEMA }));
	await page.route(/\/api\/edits(\/[^/?]+)?(\?.*)?$/, async (route) => {
		const request = route.request();
		const id = new URL(request.url()).pathname.split('/')[3] ?? null;
		const now = new Date().toISOString();
		const record = { id: id ?? EDIT_ID, tool: 'image', name: null, source: { kind: 'history', id: String(FILE_ID) }, thumbnail_upload_id: null, revision, created_at: now, updated_at: now };
		if (request.method() === 'POST' && !id) {
			saves.push(request.postDataJSON() as Json);
			return ok(route, { edit: { ...record, id: EDIT_ID } });
		}
		if (request.method() === 'GET' && id) return ok(route, { edit: { ...record, state: (saves.at(-1)?.state as Json) ?? {} } });
		if (request.method() === 'PUT' && id) {
			saves.push(request.postDataJSON() as Json);
			revision += 1;
			return ok(route, { edit: { ...record, revision } });
		}
		return route.fallback();
	});
	await page.route(/\/api\/editor\/tasks(\?.*)?$/, (route) => ok(route, { tasks: server.tasks }));
	await page.route(/\/api\/editor\/tasks\/[^/]+\/runs$/, async (route) => {
		const task = decodeURIComponent(new URL(route.request().url()).pathname.split('/').at(-2) ?? '');
		server.runs.push({ task, body: route.request().postDataJSON() as Json });
		server.runCount += 1;
		server.live = {};
		const immediate = server.immediate[task];
		await ok(route, { run: immediate ? runBody(server, 'done', { task, ...immediate }) : runBody(server, 'queued', { task }) }, 202);
	});
	await page.route(/\/api\/editor\/runs\/[^/]+$/, async (route) => {
		const status = (server.live.status as string | undefined) ?? 'queued';
		await ok(route, { run: runBody(server, status, server.live) });
	});
	await page.route(/\/api\/sessions\/preset\/[^/?]+(\?.*)?$/, (route) => ok(route, []));
	await page.route(/\/api\/formulas(\?.*)?$/, (route) => (route.request().method() === 'GET' ? ok(route, []) : route.fallback()));
	await page.routeWebSocket(/\/ws\/generation/, (ws) => {
		server.sockets.push(ws);
		ws.send(JSON.stringify({ type: 'connection_established', client_id: 'e2e-mock' }));
		ws.onMessage((raw) => {
			const message = JSON.parse(raw as string);
			if (message.type === 'subscribe_generation') ws.send(JSON.stringify({ type: 'subscribed', generation_id: message.generation_id }));
		});
	});
	return server;
}

export function update(server: TaskServer, status: string, extra: Json = {}) {
	server.live = { status, ...extra };
	for (const socket of server.sockets) socket.send(JSON.stringify({ type: 'editor_run_update', generation_id: runGeneration(server.runCount), run: runBody(server, status, extra) }));
}

export async function begin(page: Page, tasks: Json[]): Promise<TaskServer> {
	const server = await mockTasks(page, tasks);
	await loginAsOwner(page);
	await clearEdits(page);
	await page.goto('/generate');
	await expect(page.locator('.tab-bar-container')).toBeVisible({ timeout: 15000 });
	return server;
}

export async function openHarbor(page: Page) {
	await page.getByRole('button', { name: 'Add new tab' }).click();
	await page.getByRole('menu', { name: 'New tab' }).getByRole('menuitem', { name: /Image editor/ }).click();
	const empty = page.getByTestId('tool-tab-empty');
	await expect(empty).toContainText('Open a picture to edit');
	await empty.locator('[data-start-source="history"]').click();
	await page.getByTestId('history-file-tile').first().locator('[data-pick-main]').click();
	await expect(page.locator('[data-image-editor]')).toHaveAttribute('data-tool', 'crop');
}

export async function drag(page: Page, path: Array<[number, number]>): Promise<boolean> {
	const box = await page.locator('[data-mask-layer]').boundingBox();
	if (!box) return false;
	const at = ([fx, fy]: [number, number]) => ({ x: box.x + box.width * fx, y: box.y + box.height * fy });
	const [first, ...rest] = path;
	await page.mouse.move(at(first).x, at(first).y);
	await page.mouse.down();
	for (const point of rest) await page.mouse.move(at(point).x, at(point).y, { steps: 4 });
	await page.mouse.up();
	return true;
}

export async function markArea(page: Page) {
	await expect(page.locator('[data-mask-layer]')).toBeVisible();
	await expect(page.getByRole('group', { name: 'Marking tool' }).getByRole('button', { name: 'Lasso', exact: true })).toHaveAttribute('aria-pressed', 'true');
	for (let attempt = 0; attempt < 4; attempt++) {
		await page.waitForTimeout(500);
		if (await drag(page, [[0.55, 0.3], [0.85, 0.3], [0.88, 0.7], [0.58, 0.72], [0.5, 0.5]])) {
			if (await page.locator('[data-mask-area]').isVisible()) break;
		}
	}
	await expect(page.locator('[data-mask-area]')).toBeVisible();
}

export const prompt = (page: Page) => page.locator('[data-floating-prompt]');
export const row = (page: Page, id: string) => page.locator(`[data-edit-stack] [data-stack-row="${id}"]`);
export const panel = (page: Page) => page.locator('[data-options-panel]');
export const mark = (page: Page) => prompt(page).locator('[data-prompt-generate], [data-prompt-mark] button');
