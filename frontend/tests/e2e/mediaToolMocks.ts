import type { Page, Route } from '@playwright/test';
import { deflateSync } from 'node:zlib';
import { chunk } from './mediaFieldHelpers';

type Json = Record<string, unknown>;

function tool(id: string, label: string, icon: string, overrides: Json = {}): Json {
	return {
		id,
		label,
		icon,
		accepts: ['image'],
		produces: 'same',
		cardinality: 'one_to_one',
		params: [],
		requires: {},
		selection: null,
		scopes: ['library', 'history', 'field'],
		editor: null,
		preview: false,
		component: null,
		runnable: true,
		source: 'core',
		last_params: null,
		...overrides
	};
}

export const TOOL_CATALOG: Json[] = [
	tool('trim', 'Trim', 'scissors', {
		accepts: ['video', 'audio'],
		editor: 'trim',
		params: [
			{ name: 'start', type: 'float', label: 'Start', min: 0, default: 0, unit: 's' },
			{ name: 'end', type: 'float', label: 'End', min: 0, unit: 's' }
		]
	}),
	tool('crop', 'Crop', 'crop', {
		accepts: ['image', 'video'],
		editor: 'crop',
		params: ['x', 'y', 'width', 'height'].map((name) => ({ name, type: 'integer', label: name }))
	}),
	tool('resize', 'Resize', 'shrink', { accepts: ['image', 'video'], editor: 'resize' }),
	tool('rotate', 'Rotate', 'refresh', {
		accepts: ['image', 'video'],
		preview: true,
		params: [{ name: 'degrees', type: 'select', label: 'Degrees', options: [90, 180, 270], default: 90 }]
	}),
	tool('flip', 'Flip', 'refresh', {
		accepts: ['image', 'video'],
		preview: true,
		params: [
			{
				name: 'axis',
				type: 'select',
				label: 'Axis',
				options: [
					{ label: 'Horizontal', value: 'horizontal' },
					{ label: 'Vertical', value: 'vertical' }
				],
				default: 'horizontal'
			}
		]
	}),
	tool('speed', 'Speed', 'gauge', {
		accepts: ['video', 'audio'],
		params: [
			{ name: 'speed', type: 'float', label: 'Speed', min: 0.25, max: 4, step: 0.05, default: 2, unit: '×' },
			{ name: 'keep_pitch', type: 'checkbox', label: 'Keep pitch', default: true }
		]
	}),
	tool('fit_canvas', 'Fit canvas', 'expand', {
		preview: true,
		params: [
			{ name: 'width', type: 'integer', label: 'Width', min: 1, default: 1024, unit: 'px' },
			{ name: 'height', type: 'integer', label: 'Height', min: 1, default: 1024, unit: 'px' },
			{ name: 'scale_percent', type: 'float', label: 'Scale', min: 1, max: 400, step: 1, default: 100, unit: '%' },
			{ name: 'anchor', type: 'anchor', label: 'Anchor', default: 'center' },
			{ name: 'fill', type: 'color', label: 'Fill', default: 'transparent', allow_transparent: true }
		]
	}),
	tool('color_key', 'Color key', 'palette', {
		preview: true,
		params: [
			{ name: 'key_mode', type: 'select', label: 'Key', options: ['auto', 'color'], default: 'auto' },
			{ name: 'key_color', type: 'color', label: 'Key color', default: '#00B140', when: { field: 'key_mode', equals: 'color' } },
			{ name: 'tolerance', type: 'float', label: 'Tolerance', min: 0, max: 100, step: 1, default: 25 },
			{ name: 'softness', type: 'float', label: 'Softness', min: 0, max: 100, step: 1, default: 10 },
			{ name: 'despill', type: 'checkbox', label: 'Despill', default: true },
			{ name: 'feather', type: 'float', label: 'Feather', min: 0, max: 16, step: 0.5, default: 0, unit: 'px' }
		]
	}),
	tool('crop_subject', 'Crop to subject', 'crop', { requires: { has_alpha: true } }),
	tool('frame', 'Frame', 'photo', {
		accepts: ['video'],
		produces: 'image',
		editor: 'frame',
		params: [{ name: 'time', type: 'float', label: 'Time', min: 0, unit: 's' }]
	}),
	tool('split', 'Split', 'split', {
		accepts: ['audio'],
		cardinality: 'one_to_many',
		editor: 'split',
		params: [{ name: 'part_seconds', type: 'float', label: 'Part length', min: 0.1, default: 10, unit: 's' }]
	}),
	tool('concat', 'Concat', 'film', {
		accepts: ['video'],
		produces: 'video',
		cardinality: 'many_to_one',
		selection: { min: 2, max: 8 },
		params: [{ name: 'overlap', type: 'select', label: 'Overlap', options: ['cut', 'crossfade'], default: 'cut' }]
	})
];

export function catalogWith(lastParams: Record<string, Json>): Json[] {
	return TOOL_CATALOG.map((entry) =>
		lastParams[entry.id as string] ? { ...entry, last_params: lastParams[entry.id as string] } : entry
	);
}

export function resultImage(overrides: Json = {}): Json {
	return {
		id: 'result-1',
		filename: 'result-1.png',
		original_filename: 'harbour-1-edit.png',
		media_type: 'image',
		mime_type: 'image/png',
		url: '/api/e2e-media/image-9.png',
		width: 384,
		height: 256,
		size: 90000,
		...overrides
	};
}

export function resultVideo(overrides: Json = {}): Json {
	return {
		id: 'result-2',
		filename: 'result-2.webm',
		original_filename: 'clip-1-edit.webm',
		media_type: 'video',
		mime_type: 'video/webm',
		url: '/api/e2e-media/video-9.webm',
		width: 1280,
		height: 720,
		duration_seconds: 6,
		fps: 24,
		size: 400000,
		...overrides
	};
}

export interface MediaToolLog {
	catalogRequests: number;
	runs: Json[];
	previews: Json[];
	cancels: string[];
	runPolls: number;
	libraryCopies: number;
	release: () => void;
}

export interface MediaToolMockOptions {
	catalog?: Json[];
	items?: Json[];
	replaced?: boolean;
	hold?: boolean;
	refuse?: { status: number; body: Json };
	uploads?: Array<{ id: string; filename: string }>;
	generationFiles?: Record<string, Array<{ id: string; filename: string }>>;
	catalogGate?: Promise<void>;
}

export function solidPng(width: number, height: number): Buffer {
	const rows: Buffer[] = [];
	for (let y = 0; y < height; y++) {
		const row = Buffer.alloc(1 + width * 3);
		for (let x = 0; x < width; x++) {
			row[1 + x * 3] = 90 + Math.round((x * 90) / width);
			row[2 + x * 3] = 110;
			row[3 + x * 3] = 150 + Math.round((y * 60) / height);
		}
		rows.push(row);
	}
	const header = Buffer.alloc(13);
	header.writeUInt32BE(width, 0);
	header.writeUInt32BE(height, 4);
	header[8] = 8;
	header[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', header),
		chunk('IDAT', deflateSync(Buffer.concat(rows))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

export function colourPng(width: number, height: number, [red, green, blue]: [number, number, number]): Buffer {
	const row = Buffer.alloc(1 + width * 3);
	for (let x = 0; x < width; x++) {
		row[1 + x * 3] = red;
		row[2 + x * 3] = green;
		row[3 + x * 3] = blue;
	}
	const header = Buffer.alloc(13);
	header.writeUInt32BE(width, 0);
	header.writeUInt32BE(height, 4);
	header[8] = 8;
	header[9] = 2;
	return Buffer.concat([
		Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]),
		chunk('IHDR', header),
		chunk('IDAT', deflateSync(Buffer.concat(Array.from({ length: height }, () => row)))),
		chunk('IEND', Buffer.alloc(0))
	]);
}

async function fulfillJson(route: Route, data: unknown, status = 200) {
	await route.fulfill({ status, contentType: 'application/json', body: JSON.stringify({ success: true, data }) });
}

export async function mockMediaTools(page: Page, options: MediaToolMockOptions = {}): Promise<MediaToolLog> {
	let held = options.hold === true;
	let cancelled = false;
	const log: MediaToolLog = {
		catalogRequests: 0,
		runs: [],
		previews: [],
		cancels: [],
		runPolls: 0,
		libraryCopies: 0,
		release: () => {
			held = false;
		}
	};
	const preview = solidPng(96, 64);

	await page.route(/\/api\/media-tools(\?.*)?$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		log.catalogRequests += 1;
		if (options.catalogGate) await options.catalogGate;
		await fulfillJson(route, { tools: options.catalog ?? TOOL_CATALOG });
	});

	await page.route(/\/api\/media-tools\/runs$/, async (route) => {
		if (route.request().method() !== 'POST') return route.fallback();
		log.runs.push(route.request().postDataJSON() as Json);
		if (options.refuse) {
			await route.fulfill({
				status: options.refuse.status,
				contentType: 'application/json',
				body: JSON.stringify({ detail: options.refuse.body })
			});
			return;
		}
		cancelled = false;
		await fulfillJson(route, { run_id: `run-${log.runs.length}` }, 202);
	});

	await page.route(/\/api\/media-tools\/runs\/[^/]+$/, async (route) => {
		const id = route.request().url().split('/').pop() as string;
		if (route.request().method() === 'DELETE') {
			log.cancels.push(id);
			cancelled = true;
			await fulfillJson(route, {});
			return;
		}
		log.runPolls += 1;
		const base = { run_id: id, label: 'Encoding', replaced: options.replaced === true, error: null };
		if (cancelled) {
			await fulfillJson(route, { ...base, status: 'cancelled', progress: 0.4, items: [] });
		} else if (held) {
			await fulfillJson(route, { ...base, status: 'running', progress: 0.4, items: [] });
		} else {
			await fulfillJson(route, { ...base, status: 'completed', progress: 1, items: options.items ?? [resultImage()] });
		}
	});

	await page.route(/\/api\/media-tools\/preview$/, async (route) => {
		log.previews.push(route.request().postDataJSON() as Json);
		await route.fulfill({ status: 200, contentType: 'image/png', body: preview });
	});

	await page.route(/\/api\/media\/uploads(\?.*)?$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const uploads = options.uploads ?? [];
		await fulfillJson(route, { uploads, total: uploads.length, limit: 100, offset: 0 });
	});

	await page.route(/\/api\/media\/generations\/[^/]+$/, async (route) => {
		if (route.request().method() !== 'GET') return route.fallback();
		const id = route.request().url().split('/').pop() as string;
		const media = options.generationFiles?.[id] ?? [];
		await fulfillJson(route, { generation_id: id, media_count: media.length, media });
	});

	await page.route(/\/api\/library\/items\/from-generation$/, async (route) => {
		log.libraryCopies += 1;
		await route.fulfill({ status: 500, contentType: 'application/json', body: JSON.stringify({ success: false }) });
	});

	return log;
}
