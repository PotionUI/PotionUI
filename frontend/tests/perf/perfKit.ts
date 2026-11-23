import type { Browser, BrowserContext, CDPSession, Page } from '@playwright/test';
import { mkdirSync, writeFileSync } from 'node:fs';
import { join } from 'node:path';
import { AUTH_STATE_PATH, scopedStorageKey } from '../e2e/helpers';
import { discoverAndAssignImagePreset } from '../e2e/presetPreamble';

export const VIEWPORT = { width: 1440, height: 900 };

export type Metrics = Record<string, number>;

export interface RawWindow {
	t0: number;
	t1: number;
	frames: number[];
	mutations: number;
	writes: Array<{ key: string; ms: number; bytes: number }>;
	seeks: number[];
	long: Array<{ s: number; d: number }>;
	events: Array<{ n: string; s: number; d: number }>;
	inputs: Array<{ n: string; s: number; d: number }>;
}

export interface ProbeOptions {
	autoOpen: boolean;
	interactive: boolean;
}

export class PerfSkip extends Error {}

const FRAME_DROP_MS = 33.4;
const TYPING_EVENTS = new Set(['keydown', 'input']);

function installProbe(options: ProbeOptions) {
	const w = window as any;
	if (w.__perf) return;
	const state = {
		open: false,
		t0: 0,
		frames: [] as number[],
		mutations: 0,
		writes: [] as Array<{ key: string; ms: number; bytes: number }>,
		seeks: [] as number[],
		seekStarts: new WeakMap<object, number>(),
		long: [] as Array<{ s: number; d: number }>,
		events: [] as Array<{ n: string; s: number; d: number }>,
		inputs: [] as Array<{ n: string; s: number; d: number }>,
		fcp: null as number | null,
		lcp: null as number | null,
		interactive: null as number | null
	};
	const observe = (type: string, extra: Record<string, unknown>, handler: (entry: any) => void) => {
		try {
			new PerformanceObserver((list) => list.getEntries().forEach(handler)).observe({ type, buffered: true, ...extra } as any);
		} catch {
			return;
		}
	};
	observe('longtask', {}, (entry) => state.long.push({ s: entry.startTime, d: entry.duration }));
	observe('event', { durationThreshold: 16 }, (entry) => state.events.push({ n: entry.name, s: entry.startTime, d: entry.duration }));
	observe('paint', {}, (entry) => {
		if (entry.name === 'first-contentful-paint') state.fcp = entry.startTime;
	});
	observe('largest-contentful-paint', {}, (entry) => {
		state.lcp = entry.startTime;
	});

	for (const name of ['keydown', 'input', 'pointerdown', 'pointerup', 'click']) {
		window.addEventListener(
			name,
			(event) => {
				if (!state.open) return;
				const started = event.timeStamp;
				requestAnimationFrame(() => setTimeout(() => state.inputs.push({ n: name, s: started, d: performance.now() - started }), 0));
			},
			true
		);
	}

	new MutationObserver((records) => {
		if (state.open) state.mutations += records.length;
	}).observe(document, { subtree: true, childList: true, attributes: true, characterData: true });

	const originalSetItem = Storage.prototype.setItem;
	Storage.prototype.setItem = function (key: string, value: string) {
		const started = performance.now();
		const result = originalSetItem.call(this, key, value);
		if (state.open) state.writes.push({ key: String(key), ms: performance.now() - started, bytes: String(value).length });
		return result;
	};

	document.addEventListener('seeking', (event) => state.seekStarts.set(event.target as object, performance.now()), true);
	document.addEventListener('seeked', (event) => {
		const started = state.seekStarts.get(event.target as object);
		if (state.open && started !== undefined) state.seeks.push(performance.now() - started);
	}, true);

	const tick = (time: number) => {
		if (!state.open) return;
		state.frames.push(time);
		requestAnimationFrame(tick);
	};
	const begin = () => {
		state.t0 = performance.now();
		state.frames = [];
		state.mutations = 0;
		state.writes = [];
		state.seeks = [];
		state.inputs = [];
		state.open = true;
		requestAnimationFrame(tick);
	};
	const end = (): RawWindow => {
		state.open = false;
		const t1 = performance.now();
		const inside = (start: number) => start >= state.t0 && start <= t1;
		return {
			t0: state.t0,
			t1,
			frames: state.frames.slice(),
			mutations: state.mutations,
			writes: state.writes.slice(),
			seeks: state.seeks.slice(),
			long: state.long.filter((entry) => inside(entry.s)),
			events: state.events.filter((entry) => inside(entry.s)),
			inputs: state.inputs.filter((entry) => inside(entry.s))
		};
	};

	const visibleEditorText = () => {
		const editors = Array.from(document.querySelectorAll<HTMLElement>('.inline-chip-editor[role="textbox"]'));
		const editor = editors.find((node) => node.offsetParent !== null);
		return editor ? editor.textContent ?? '' : '';
	};
	const nextPaint = () => new Promise<void>((resolve) => requestAnimationFrame(() => requestAnimationFrame(() => resolve())));
	const switchTab = async (index: number, token: string, timeoutMs: number) => {
		const tabs = Array.from(document.querySelectorAll<HTMLElement>('button.book-tab'));
		const target = tabs[index];
		if (!target) return -1;
		const started = performance.now();
		target.click();
		while (!visibleEditorText().includes(token)) {
			if (performance.now() - started > timeoutMs) return -1;
			await new Promise<void>((resolve) => requestAnimationFrame(() => resolve()));
		}
		await nextPaint();
		return performance.now() - started;
	};

	if (options.interactive) {
		const poll = () => {
			const mark = performance.getEntriesByName('potionui:runtime-ready')[0];
			if (mark) {
				state.interactive = mark.startTime;
				return;
			}
			const form = document.querySelector('section[aria-label] > div.space-y-4');
			const loading = document.body?.textContent?.includes('Loading Form...');
			if (form && !loading) {
				state.interactive = performance.now();
				return;
			}
			requestAnimationFrame(poll);
		};
		requestAnimationFrame(poll);
	}
	if (options.autoOpen) {
		state.open = true;
		state.t0 = 0;
		requestAnimationFrame(tick);
	}

	w.__perf = {
		begin,
		end,
		switchTab,
		state,
		observed: () => ({ fcp: state.fcp, lcp: state.lcp, interactive: state.interactive })
	};
}

export function percentile(values: number[], fraction: number): number {
	if (!values.length) return 0;
	const sorted = [...values].sort((a, b) => a - b);
	const index = Math.min(sorted.length - 1, Math.max(0, Math.ceil(fraction * sorted.length) - 1));
	return sorted[index];
}

export function median(values: number[]): number {
	return percentile(values, 0.5);
}

const sum = (values: number[]) => values.reduce((total, value) => total + value, 0);

export function windowMetrics(raw: RawWindow, cdp: Metrics): Metrics {
	const deltas: number[] = [];
	for (let i = 1; i < raw.frames.length; i++) deltas.push(raw.frames[i] - raw.frames[i - 1]);
	const frames = deltas.length;
	const longDurations = raw.long.map((entry) => entry.d);
	return {
		wall_ms: raw.t1 - raw.t0,
		long_task_count: raw.long.length,
		long_task_total_ms: sum(longDurations),
		tbt_ms: sum(longDurations.map((duration) => Math.max(0, duration - 50))),
		long_task_max_ms: longDurations.length ? Math.max(...longDurations) : 0,
		...cdp,
		frames,
		frame_p50_ms: percentile(deltas, 0.5),
		frame_p95_ms: percentile(deltas, 0.95),
		dropped_frames: deltas.filter((delta) => delta > FRAME_DROP_MS).length,
		script_ms_per_frame: frames ? (cdp.script_ms ?? 0) / frames : 0,
		mutations_per_frame: frames ? raw.mutations / frames : 0
	};
}

export function typingMetrics(raw: RawWindow): Metrics {
	const durations = raw.inputs.filter((entry) => TYPING_EVENTS.has(entry.n)).map((entry) => entry.d);
	return {
		event_count: durations.length,
		event_slow_count: raw.events.filter((entry) => TYPING_EVENTS.has(entry.n)).length,
		event_p95_ms: percentile(durations, 0.95),
		event_max_ms: durations.length ? Math.max(...durations) : 0
	};
}

export function persistMetrics(raw: RawWindow, keyPart: string): Metrics {
	const writes = raw.writes.filter((write) => write.key.includes(keyPart));
	return {
		persist_writes: writes.length,
		persist_ms: sum(writes.map((write) => write.ms)),
		persist_kb: sum(writes.map((write) => write.bytes)) / 1024
	};
}

export function seekMetrics(raw: RawWindow): Metrics {
	return {
		seek_count: raw.seeks.length,
		seek_p95_ms: percentile(raw.seeks, 0.95),
		seek_max_ms: raw.seeks.length ? Math.max(...raw.seeks) : 0
	};
}

const CDP_FIELDS: Record<string, string> = {
	ScriptDuration: 'script_ms',
	LayoutDuration: 'layout_ms',
	RecalcStyleDuration: 'style_ms',
	TaskDuration: 'task_ms'
};

export interface NetworkSummary {
	requests: number;
	byType: Record<string, { count: number; bytes: number }>;
	jsFiles: Array<{ url: string; bytes: number }>;
	compression: string;
}

export interface Kit {
	page: Page;
	context: BrowserContext;
	cdp: CDPSession;
	run: number;
	heapMb(): Promise<number>;
	snapshot(): Promise<Metrics>;
	begin(): Promise<void>;
	end(before?: Metrics): Promise<{ raw: RawWindow; metrics: Metrics }>;
	window<T>(fn: () => Promise<T>): Promise<{ value: T; raw: RawWindow; metrics: Metrics }>;
	observed(): Promise<{ fcp: number | null; lcp: number | null; interactive: number | null }>;
	network(): NetworkSummary;
	extra: Record<string, unknown>;
	tracing: boolean;
}

async function readCdp(cdp: CDPSession): Promise<Metrics> {
	const { metrics } = await cdp.send('Performance.getMetrics');
	const out: Metrics = {};
	for (const metric of metrics) {
		if (CDP_FIELDS[metric.name]) out[CDP_FIELDS[metric.name]] = metric.value * 1000;
		if (metric.name === 'JSHeapUsedSize') out.heap_used_mb = metric.value / (1024 * 1024);
	}
	return out;
}

function cdpDelta(before: Metrics, after: Metrics): Metrics {
	const out: Metrics = {};
	for (const field of Object.values(CDP_FIELDS)) out[field] = (after[field] ?? 0) - (before[field] ?? 0);
	out.heap_used_mb = after.heap_used_mb ?? 0;
	return out;
}

function watchNetwork(cdp: CDPSession) {
	const types = new Map<string, string>();
	const urls = new Map<string, string>();
	const headers = new Map<string, string>();
	const summary: NetworkSummary = { requests: 0, byType: {}, jsFiles: [], compression: 'none' };
	cdp.on('Network.requestWillBeSent', (event: any) => {
		summary.requests += 1;
		urls.set(event.requestId, event.request.url);
	});
	cdp.on('Network.responseReceived', (event: any) => {
		types.set(event.requestId, event.type);
		const encoding = event.response.headers['content-encoding'] ?? event.response.headers['Content-Encoding'];
		if (encoding) headers.set(event.requestId, String(encoding));
	});
	cdp.on('Network.loadingFinished', (event: any) => {
		const type = types.get(event.requestId) ?? 'Other';
		const entry = (summary.byType[type] ??= { count: 0, bytes: 0 });
		entry.count += 1;
		entry.bytes += event.encodedDataLength;
		if (type === 'Script') summary.jsFiles.push({ url: urls.get(event.requestId) ?? '', bytes: event.encodedDataLength });
		if (headers.has(event.requestId)) summary.compression = headers.get(event.requestId)!;
	});
	return summary;
}

export function networkMetrics(summary: NetworkSummary, apiRequests: number): Metrics {
	const kb = (type: string) => (summary.byType[type]?.bytes ?? 0) / 1024;
	const total = Object.values(summary.byType).reduce((acc, entry) => acc + entry.bytes, 0) / 1024;
	return {
		requests: summary.requests,
		js_kb: kb('Script'),
		css_kb: kb('Stylesheet'),
		font_kb: kb('Font'),
		total_kb: total,
		js_requests: summary.byType.Script?.count ?? 0,
		api_requests: apiRequests
	};
}

export interface Scenario<S> {
	name: string;
	probe?: Partial<ProbeOptions>;
	viewport?: { width: number; height: number };
	setup?: (browser: Browser) => Promise<S>;
	configure?: (context: BrowserContext, shared: S) => Promise<void>;
	prepare?: (kit: Kit, shared: S) => Promise<void>;
	measure: (kit: Kit, shared: S) => Promise<Metrics>;
	trackNetwork?: boolean;
}

function intFromEnv(name: string, fallback: number): number {
	const parsed = Number.parseInt(process.env[name] ?? '', 10);
	return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function numberFromEnv(name: string, fallback: number): number {
	const parsed = Number.parseFloat(process.env[name] ?? '');
	return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

export function aggregate(runs: Metrics[]): Record<string, { median: number; min: number; max: number; runs: number[] }> {
	const keys = new Set<string>();
	for (const run of runs) Object.keys(run).forEach((key) => keys.add(key));
	const out: Record<string, { median: number; min: number; max: number; runs: number[] }> = {};
	for (const key of keys) {
		const values = runs.map((run) => run[key]).filter((value) => typeof value === 'number' && Number.isFinite(value));
		if (!values.length) continue;
		out[key] = { median: median(values), min: Math.min(...values), max: Math.max(...values), runs: values };
	}
	return out;
}

function writePart(name: string, payload: Record<string, unknown>) {
	const dir = process.env.PERF_OUT_DIR;
	const body = JSON.stringify({ name, ...payload }, null, 2);
	if (!dir) {
		console.log(body);
		return;
	}
	mkdirSync(dir, { recursive: true });
	writeFileSync(join(dir, `${name}.json`), body);
}

const LOCAL_HOSTS = new Set(['127.0.0.1', 'localhost', '[::1]']);

async function newMeasuredContext<S>(browser: Browser, scenario: Scenario<S>, shared: S): Promise<BrowserContext> {
	const viewport = scenario.viewport ?? VIEWPORT;
	const context = await browser.newContext({
		baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:4173',
		storageState: AUTH_STATE_PATH,
		viewport,
		deviceScaleFactor: 1,
		reducedMotion: 'no-preference'
	});
	await context.route('**/*', (route) => {
		const host = new URL(route.request().url()).hostname;
		if (LOCAL_HOSTS.has(host) || host === '' || route.request().url().startsWith('data:') || route.request().url().startsWith('blob:')) return route.fallback();
		return route.abort();
	});
	await context.addInitScript(installProbe, { autoOpen: false, interactive: false, ...scenario.probe });
	if (scenario.configure) await scenario.configure(context, shared);
	return context;
}

async function buildKit(browser: Browser, context: BrowserContext, page: Page, run: number, throttle: number, trackNetwork: boolean, tracePath: string | null): Promise<Kit> {
	const cdp = await context.newCDPSession(page);
	await cdp.send('Performance.enable');
	await cdp.send('HeapProfiler.enable');
	if (throttle !== 1) await cdp.send('Emulation.setCPUThrottlingRate', { rate: throttle });
	let summary: NetworkSummary = { requests: 0, byType: {}, jsFiles: [], compression: 'none' };
	if (trackNetwork) {
		await cdp.send('Network.enable');
		summary = watchNetwork(cdp);
	}

	const kit: Kit = {
		page,
		context,
		cdp,
		run,
		extra: {},
		tracing: false,
		async heapMb() {
			await cdp.send('HeapProfiler.collectGarbage');
			return (await readCdp(cdp)).heap_used_mb ?? 0;
		},
		snapshot: () => readCdp(cdp),
		async begin() {
			await cdp.send('HeapProfiler.collectGarbage');
			(kit as any)._before = await readCdp(cdp);
			if (tracePath) {
				await browser.startTracing(page, {
					path: tracePath,
					categories: ['devtools.timeline', 'disabled-by-default-devtools.timeline.frame', 'v8.execute']
				});
				kit.tracing = true;
			}
			await page.evaluate(() => (window as any).__perf.begin());
		},
		async end(before) {
			const raw = (await page.evaluate(() => (window as any).__perf.end())) as RawWindow;
			const after = await readCdp(cdp);
			if (tracePath) {
				await browser.stopTracing();
				kit.tracing = false;
			}
			const metrics = windowMetrics(raw, cdpDelta(before ?? (kit as any)._before ?? {}, after));
			return { raw, metrics };
		},
		async window(fn) {
			await kit.begin();
			const value = await fn();
			const { raw, metrics } = await kit.end();
			return { value, raw, metrics };
		},
		observed: () => page.evaluate(() => (window as any).__perf.observed()),
		network: () => summary
	};
	return kit;
}

export async function runScenario<S>(browser: Browser, scenario: Scenario<S>): Promise<void> {
	const runs = intFromEnv('PERF_RUNS', 5);
	const throttle = numberFromEnv('PERF_CPU_THROTTLE', 1);
	const traceDir = process.env.PERF_TRACE === '1' && process.env.PERF_OUT_DIR ? process.env.PERF_OUT_DIR : null;
	const viewport = scenario.viewport ?? VIEWPORT;
	const meta = {
		chromium: browser.version(),
		viewport: `${viewport.width}x${viewport.height}`,
		throttle
	};
	let shared = undefined as S;
	try {
		if (scenario.setup) shared = await scenario.setup(browser);
	} catch (error) {
		if (error instanceof PerfSkip) {
			writePart(scenario.name, { status: 'skipped', reason: error.message, runs: 0, meta, metrics: {} });
			return;
		}
		writePart(scenario.name, { status: 'failed', reason: `setup: ${(error as Error).message}`, runs: 0, meta, metrics: {} });
		throw error;
	}

	const measured: Metrics[] = [];
	let extra: Record<string, unknown> = {};
	for (let run = 0; run <= runs; run++) {
		const context = await newMeasuredContext(browser, scenario, shared);
		let kit: Kit | null = null;
		try {
			const page = await context.newPage();
			const tracePath = traceDir && run > 0 ? join(traceDir, `${scenario.name}-${run}.trace.json`) : null;
			kit = await buildKit(browser, context, page, run, throttle, scenario.trackNetwork === true, tracePath);
			if (scenario.prepare) await scenario.prepare(kit, shared);
			const metrics = await scenario.measure(kit, shared);
			if (run > 0) {
				measured.push(metrics);
				extra = kit.extra;
			}
		} catch (error) {
			if (error instanceof PerfSkip) {
				writePart(scenario.name, { status: 'skipped', reason: error.message, runs: 0, meta, metrics: {} });
				return;
			}
			writePart(scenario.name, { status: 'failed', reason: `run ${run}: ${(error as Error).message}`, runs: measured.length, meta, metrics: {} });
			throw error;
		} finally {
			if (kit?.tracing) await browser.stopTracing().catch(() => undefined);
			await context.close().catch(() => undefined);
		}
	}
	writePart(scenario.name, { status: 'ok', runs: measured.length, meta, metrics: aggregate(measured), extra });
}

export interface PresetHandle {
	presetId: string;
	mode: string;
	tabsKey: string;
}

export async function discoverPreset(browser: Browser): Promise<PresetHandle> {
	const context = await browser.newContext({
		baseURL: process.env.E2E_BASE_URL || 'http://127.0.0.1:4173',
		storageState: AUTH_STATE_PATH,
		viewport: VIEWPORT
	});
	try {
		const page = await context.newPage();
		await page.goto('/generate');
		await page.waitForLoadState('networkidle');
		const preset = await discoverAndAssignImagePreset(page);
		if (!preset) throw new PerfSkip('no native image preset available on this throwaway instance');
		const token = await page.evaluate(() => localStorage.getItem('auth_token'));
		const response = await page.request.get(`/api/presets/${preset.id}/modes`, { headers: { Authorization: `Bearer ${token}` } });
		const body = await response.json().catch(() => ({}));
		const mode = body?.data?.default_mode || body?.data?.modes?.[0]?.name;
		if (!mode) throw new PerfSkip(`preset ${preset.id} exposes no mode`);
		return { presetId: preset.id, mode, tabsKey: await scopedStorageKey(page, 'potionui_tabs_state') };
	} finally {
		await context.close();
	}
}

export interface SeedTab {
	id: string;
	name: string;
	prompt: string;
}

export async function seedTabsState(context: BrowserContext, key: string, state: Record<string, unknown>): Promise<void> {
	await context.addInitScript(
		({ storageKey, value }) => {
			try {
				if (sessionStorage.getItem('perf-seeded')) return;
				sessionStorage.setItem('perf-seeded', '1');
				localStorage.setItem(storageKey, JSON.stringify(value));
			} catch {
				return;
			}
		},
		{ storageKey: key, value: state }
	);
}

export function generationTab(handle: PresetHandle, tab: SeedTab): Record<string, unknown> {
	return {
		id: tab.id,
		name: tab.name,
		selectedPreset: handle.presetId,
		selectedMode: handle.mode,
		selectedVariant: null,
		selectedSessionId: null,
		activeGenerationId: null,
		prompt: tab.prompt,
		negativePrompt: '',
		promptSegments: [],
		negativePromptSegments: [],
		formData: {}
	};
}

export async function seedGenerationTabs(context: BrowserContext, handle: PresetHandle, tabs: SeedTab[], activeIndex = 0): Promise<void> {
	const payload = tabs.map((tab) => generationTab(handle, tab));
	await seedTabsState(context, handle.tabsKey, { tabs: payload, activeTabId: tabs[activeIndex].id });
}
