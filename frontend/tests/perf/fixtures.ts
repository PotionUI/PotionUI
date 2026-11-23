import type { BrowserContext, Page, Route } from '@playwright/test';
import { execFileSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { existsSync, mkdirSync, readFileSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import { deflateSync } from 'node:zlib';
import { chunk } from '../e2e/mediaFieldHelpers';
import { PerfSkip } from './perfKit';

export const SEED = 0x9e3779b9;

export function rng(seed: number = SEED): () => number {
	let state = seed >>> 0;
	return () => {
		state = (state + 0x6d2b79f5) >>> 0;
		let t = state;
		t = Math.imul(t ^ (t >>> 15), t | 1);
		t ^= t + Math.imul(t ^ (t >>> 7), t | 61);
		return ((t ^ (t >>> 14)) >>> 0) / 4294967296;
	};
}

export function cacheDir(tag: string): string {
	const hash = createHash('sha1').update(tag).digest('hex').slice(0, 12);
	const dir = join(tmpdir(), 'potionui-perf', hash);
	mkdirSync(dir, { recursive: true });
	return dir;
}

function rawPng(width: number, height: number, pixel: (x: number, y: number, channel: number) => number, level = 1): Buffer {
	const stride = 1 + width * 3;
	const raw = Buffer.alloc(stride * height);
	for (let y = 0; y < height; y++) {
		const base = y * stride;
		for (let x = 0; x < width; x++) {
			const offset = base + 1 + x * 3;
			raw[offset] = pixel(x, y, 0);
			raw[offset + 1] = pixel(x, y, 1);
			raw[offset + 2] = pixel(x, y, 2);
		}
	}
	const header = Buffer.alloc(13);
	header.writeUInt32BE(width, 0);
	header.writeUInt32BE(height, 4);
	header[8] = 8;
	header[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', header),
		chunk('IDAT', deflateSync(raw, { level })),
		chunk('IEND', Buffer.alloc(0))
	]);
}

function cachedFile(tag: string, name: string, build: () => Buffer): Buffer {
	const path = join(cacheDir(tag), name);
	if (existsSync(path) && statSync(path).size > 0) return readFileSync(path);
	const data = build();
	writeFileSync(path, data);
	return data;
}

export const PREVIEW_COUNT = 20;
const PREVIEW_SIZE = 1024;

export function previewImages(): string[] {
	const tag = `previews-v1-${PREVIEW_SIZE}-${PREVIEW_COUNT}`;
	const path = join(cacheDir(tag), 'previews.json');
	if (existsSync(path)) {
		const cached = JSON.parse(readFileSync(path, 'utf8')) as string[];
		if (cached.length === PREVIEW_COUNT) return cached;
	}
	const images: string[] = [];
	for (let index = 0; index < PREVIEW_COUNT; index++) {
		const random = rng(SEED + index);
		const noise = new Uint8Array(PREVIEW_SIZE * PREVIEW_SIZE);
		for (let i = 0; i < noise.length; i++) noise[i] = Math.floor(random() * 8);
		const phase = index * 13;
		const png = rawPng(PREVIEW_SIZE, PREVIEW_SIZE, (x, y, channel) => {
			const wave = 64 + 48 * Math.sin((x + phase) / (90 + channel * 25)) + 40 * Math.cos((y - phase) / (110 + channel * 15));
			return Math.max(0, Math.min(255, Math.round(wave + noise[y * PREVIEW_SIZE + x])));
		});
		images.push(`data:image/png;base64,${png.toString('base64')}`);
	}
	writeFileSync(path, JSON.stringify(images));
	return images;
}

export function bigImagePng(width = 3840, height = 2160): Buffer {
	return cachedFile(`big-image-v1-${width}x${height}`, 'image.png', () => {
		const random = rng(SEED);
		const grain = new Uint8Array(4096);
		for (let i = 0; i < grain.length; i++) grain[i] = Math.floor(random() * 6);
		return rawPng(width, height, (x, y, channel) => {
			const base = (x * (80 + channel * 40)) / width + (y * (60 + channel * 30)) / height;
			return Math.max(0, Math.min(255, Math.round(40 + base + grain[(x * 7 + y * 13 + channel) & 4095])));
		});
	});
}

export function thumbnailPng(width = 320, height = 200): Buffer {
	return cachedFile(`thumb-v1-${width}x${height}`, 'thumb.png', () =>
		rawPng(width, height, (x, y, channel) => Math.round(60 + (x / width) * 100 + (y / height) * 60 + channel * 12), 6)
	);
}

export function makeClip(): string {
	const ffmpeg = process.env.PERF_FFMPEG;
	if (!ffmpeg) throw new PerfSkip('ffmpeg could not be resolved (no imageio_ffmpeg in the venv and none on PATH)');
	const dir = cacheDir('clip-v1-1920x1080-10s-g30');
	const path = join(dir, 'clip.mp4');
	if (existsSync(path) && statSync(path).size > 0) return path;
	try {
		execFileSync(
			ffmpeg,
			[
				'-y', '-loglevel', 'error',
				'-f', 'lavfi', '-i', 'testsrc2=size=1920x1080:rate=30',
				'-t', '10',
				'-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '30', '-g', '30',
				'-pix_fmt', 'yuv420p', '-movflags', '+faststart',
				path
			],
			{ stdio: 'pipe' }
		);
	} catch (error) {
		throw new PerfSkip(`ffmpeg failed to encode the fixture clip: ${(error as Error).message}`);
	}
	return path;
}

export async function serveRanged(target: Page | BrowserContext, pattern: string | RegExp, file: string, contentType: string): Promise<void> {
	const body = readFileSync(file);
	await target.route(pattern, (route: Route) => {
		const range = route.request().headers()['range'];
		const base = { 'Content-Type': contentType, 'Accept-Ranges': 'bytes' };
		const match = range ? /bytes=(\d*)-(\d*)/.exec(range) : null;
		if (!match) return route.fulfill({ status: 200, headers: { ...base, 'Content-Length': String(body.length) }, body });
		const start = match[1] ? Number.parseInt(match[1], 10) : 0;
		const end = match[2] ? Math.min(Number.parseInt(match[2], 10), body.length - 1) : body.length - 1;
		const slice = body.subarray(start, end + 1);
		return route.fulfill({
			status: 206,
			headers: { ...base, 'Content-Range': `bytes ${start}-${end}/${body.length}`, 'Content-Length': String(slice.length) },
			body: slice
		});
	});
}

const LEAF_TYPES = new Set(['slider', 'number', 'integer', 'select', 'boolean', 'checkbox', 'stepper']);

function collectLeaves(node: unknown, out: Array<Record<string, any>>, groups: Array<{ children: unknown[] }>): void {
	if (Array.isArray(node)) {
		node.forEach((child) => collectLeaves(child, out, groups));
		return;
	}
	if (!node || typeof node !== 'object') return;
	const record = node as Record<string, any>;
	if (Array.isArray(record.children)) groups.push(record as { children: unknown[] });
	if (typeof record.name === 'string' && LEAF_TYPES.has(record.type)) out.push(record);
	Object.values(record).forEach((child) => collectLeaves(child, out, groups));
}

export const EXPANDED_FIELDS = 60;
const EXPANDED_GROUPS = 5;

export function expandForm(body: any, total = EXPANDED_FIELDS): number {
	const root = body?.data ?? body;
	const schema = root?.form_schema ?? root;
	const leaves: Array<Record<string, any>> = [];
	const groups: Array<{ children: unknown[] }> = [];
	collectLeaves(schema, leaves, groups);
	const sliders = leaves.filter((leaf) => leaf.type === 'slider');
	const numbers = leaves.filter((leaf) => leaf.type === 'number' || leaf.type === 'integer' || leaf.type === 'stepper');
	const selects = leaves.filter((leaf) => leaf.type === 'select');
	const toggles = leaves.filter((leaf) => leaf.type === 'boolean' || leaf.type === 'checkbox');
	const pools = [sliders, sliders, numbers, selects, toggles].filter((pool) => pool.length);
	if (!pools.length) return 0;

	const clones: Array<Record<string, any>> = [];
	for (let index = 0; clones.length < total; index++) {
		const pool = pools[index % pools.length];
		const source = pool[Math.floor(index / pools.length) % pool.length];
		clones.push({ ...JSON.parse(JSON.stringify(source)), name: `perf_${source.name}_${index}`, title: `${source.title ?? source.name} ${index}` });
	}

	const perGroup = Math.ceil(clones.length / EXPANDED_GROUPS);
	const created: Array<Record<string, any>> = [];
	for (let g = 0; g < EXPANDED_GROUPS; g++) {
		created.push({
			name: `perf_group_${g}`,
			type: 'group',
			title: `Perf group ${g + 1}`,
			children: clones.slice(g * perGroup, (g + 1) * perGroup)
		});
	}
	const properties = schema?.properties;
	if (properties && typeof properties === 'object' && !Array.isArray(properties)) {
		for (const group of created) properties[group.name] = group;
	} else if (groups.length) {
		groups[0].children.push(...created);
	} else {
		return 0;
	}
	return clones.length;
}

export function relaxModelFields(node: unknown): void {
	if (Array.isArray(node)) return node.forEach(relaxModelFields);
	if (node && typeof node === 'object') {
		const record = node as Record<string, unknown>;
		if (record.type === 'model' && 'required' in record) record.required = false;
		Object.values(record).forEach(relaxModelFields);
	}
}

export async function installExpandedForm(target: Page | BrowserContext, stats: { fields: number } = { fields: 0 }): Promise<void> {
	await target.route(/\/api\/presets\/[^/]+\/form(\?.*)?$/, async (route) => {
		const response = await route.fetch();
		const body = await response.json();
		relaxModelFields(body);
		stats.fields = expandForm(body);
		await route.fulfill({ response, json: body });
	});
}

const LOREM = ['amber', 'lantern', 'harbor', 'violet', 'observatory', 'glass', 'meadow', 'copper', 'ember', 'drifting', 'quiet', 'mosaic', 'tidal', 'orchard', 'winter', 'signal'];

export function typedPrompt(length = 800): string {
	const random = rng(SEED + 7);
	const words: string[] = ['a ${subject} under {warm|cold|pale} light,'];
	let text = words[0];
	while (text.length < length) {
		const word = LOREM[Math.floor(random() * LOREM.length)];
		text += random() < 0.08 ? ` {${word}|${LOREM[Math.floor(random() * LOREM.length)]}}` : ` ${word}`;
	}
	return text.slice(0, length);
}

export function historyRecords(count = 96): Array<Record<string, unknown>> {
	const random = rng(SEED + 3);
	const now = Date.now();
	const records: Array<Record<string, unknown>> = [];
	for (let index = 0; index < count; index++) {
		const id = `01PERFHISTORY${String(index).padStart(13, '0')}`;
		const prompt = `${LOREM[Math.floor(random() * LOREM.length)]} ${LOREM[Math.floor(random() * LOREM.length)]} study ${index}`;
		const created = new Date(now - index * 60000).toISOString();
		records.push({
			id,
			preset_id: 'perf-preset',
			preset_name: 'Perf preset',
			mode: 'txt2img',
			form_name: null,
			form_data: { prompt, steps: 28, cfg: 3.5, seed: Math.floor(random() * 1e9), width: 1280, height: 800, quantity: 2, sampler: 'euler' },
			status: 'completed',
			progress: 1,
			created_at: created,
			started_at: created,
			completed_at: created,
			run_seconds: 30,
			queued_seconds: 1,
			updated_at: created,
			files: [0, 1].map((file) => ({
				id: index * 10 + file + 1,
				file_path: `/outputs/${id}/${file}.png`,
				file_type: 'image',
				width: 1280,
				height: 800,
				is_final: true,
				is_derived: false
			})),
			segments: [],
			tags: [],
			rating: 0,
			is_favorite: false,
			origin: null
		});
	}
	return records;
}
