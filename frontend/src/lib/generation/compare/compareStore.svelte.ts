import { get, writable } from 'svelte/store';
import { tabsStore } from '$lib/stores/tabs';
import { toasts } from '$lib/stores/toast';
import { api } from '$lib/services/api/index';
import type { GenerationRequest } from '$lib/services/api/index';
import { refusalFromError } from '$lib/plans/refusal';
import { hasAxisEditor } from '$lib/fields/registry';
import { applyCellMessage, COMPARE_MESSAGE_TYPES, COMPARE_TERMINAL_TYPES, generationIdOf } from './compareMessages';
import { buildAxisCandidates } from './candidates';
import { cellCount, effectiveAxes } from './axisValues';
import { shortfallFromError, type DailyShortfall } from './compareLimits';
import { deriveCompareSummary, type CompareSummary } from './compareSummary';
import type { LimitRow } from '$lib/plans/meApi';
import { DEFAULT_CONTACT_LINE } from '$lib/plans/refusal';
import { fetchGrid, fetchGridSettings, postGrid, postRetryFailed, removeGrid } from './compareApi';
import { gridProgress } from './view/gridModel';
import { cellAxisValues, emptyCell, gridFromServer } from './serverGrid';
import { quantityFieldsOf } from './compareSchema';
import {
	DEFAULT_GRID_SETTINGS,
	PROMPT_AXIS_FIELD,
	emptyCompareConfig,
	type ActiveGrid,
	type AxisCandidate,
	type CompareAxis,
	type CompareConfig,
	type GridCell,
	type GridSettings,
	type ServerGrid
} from './types';

export { cellCount } from './axisValues';
export type { ActiveGrid, CompareAxis, CompareConfig, GridCell } from './types';

const EMPTY_CONFIG: CompareConfig = Object.freeze(emptyCompareConfig()) as CompareConfig;
const CELL_MS_KEY = 'potionui_compare_cell_ms';
const REFRESH_DELAY_MS = 800;
const TERMINAL = new Set(['completed', 'failed', 'cancelled', 'deleted']);

let configs = $state<Record<string, CompareConfig>>({});
let liveGrids = $state<Record<string, ActiveGrid | null>>({});
let blockers = $state<Record<string, string | null>>({});
let refusals = $state<Record<string, DailyShortfall | null>>({});
let submitting = $state<Record<string, boolean>>({});
let drawerTabId = $state<string | null>(null);
let gridSettings = $state<GridSettings>({ ...DEFAULT_GRID_SETTINGS });
let schemas = $state.raw<Record<string, { key: string; schema: unknown }>>({});
let cellMs = $state<Record<string, number>>(loadCellMs());

const revision = writable(0);

export const compareRevision = { subscribe: revision.subscribe };

function touch(): void {
	revision.update((value) => value + 1);
}

const seenConfig = new Map<string, CompareConfig | undefined>();
const reconciledKeys = new Map<string, string>();
const generationIndex = new Map<string, { tabId: string; index: number }>();
const runStarted = new Map<string, number>();
const refreshTimers = new Map<string, ReturnType<typeof setTimeout>>();
let settingsLoaded = false;

export type GridRunHandlers = {
	enrolled: (tabId: string, generationIds: string[]) => void;
	cancelled: (tabId: string, generationIds: string[]) => void;
};

let runHandlers: GridRunHandlers | null = null;

export function setGridRunHandlers(handlers: GridRunHandlers | null): void {
	runHandlers = handlers;
}

function loadCellMs(): Record<string, number> {
	try {
		if (typeof localStorage === 'undefined') return {};
		const raw = localStorage.getItem(CELL_MS_KEY);
		const parsed = raw ? JSON.parse(raw) : {};
		return parsed && typeof parsed === 'object' ? parsed : {};
	} catch {
		return {};
	}
}

function saveCellMs(): void {
	try {
		if (typeof localStorage !== 'undefined') localStorage.setItem(CELL_MS_KEY, JSON.stringify(cellMs));
	} catch {
		return;
	}
}

function findTab(tabId: string) {
	return get(tabsStore).tabs.find((tab) => tab.id === tabId);
}

function normalizeConfig(raw: Partial<CompareConfig> | undefined | null): CompareConfig {
	return {
		armed: raw?.armed === true,
		x: raw?.x ?? null,
		y: raw?.y ?? null,
		lockSeed: raw?.lockSeed !== false
	};
}

tabsStore.subscribe((state) => {
	const alive = new Set<string>();
	for (const tab of state.tabs) {
		alive.add(tab.id);
		if (seenConfig.get(tab.id) === tab.compare && seenConfig.has(tab.id)) continue;
		seenConfig.set(tab.id, tab.compare);
		configs[tab.id] = tab.compare ? normalizeConfig(tab.compare) : { ...EMPTY_CONFIG };
	}
	for (const id of [...seenConfig.keys()]) {
		if (alive.has(id)) continue;
		seenConfig.delete(id);
		delete configs[id];
		delete liveGrids[id];
		delete blockers[id];
		delete refusals[id];
		reconciledKeys.delete(id);
		for (const [generationId, entry] of generationIndex) {
			if (entry.tabId === id) generationIndex.delete(generationId);
		}
	}
	touch();
});

export function getCompare(tabId: string): CompareConfig {
	return configs[tabId] ?? EMPTY_CONFIG;
}

export function seedAxisActive(config: CompareConfig): boolean {
	return config.x?.field === 'seed' || config.y?.field === 'seed';
}

export function effectiveLockSeed(config: CompareConfig): boolean {
	return config.lockSeed && !seedAxisActive(config);
}

function commitConfig(tabId: string, next: CompareConfig): void {
	configs[tabId] = next;
	seenConfig.set(tabId, next);
	tabsStore.updateTab(tabId, { compare: next });
	touch();
}

export function setCompare(tabId: string, patch: Partial<CompareConfig>): void {
	const current = $state.snapshot(getCompare(tabId)) as CompareConfig;
	const merged = normalizeConfig({ ...current, ...patch });
	const axesChanged = 'x' in patch || 'y' in patch;
	commitConfig(tabId, merged);
	if (axesChanged && !isGridRunning(tabId)) {
		const live = liveGrids[tabId];
		if (live) liveGrids[tabId] = null;
		touch();
	}
}

export function setAxis(tabId: string, slot: 'x' | 'y', axis: CompareAxis | null): void {
	const current = $state.snapshot(getCompare(tabId)) as CompareConfig;
	const other: 'x' | 'y' = slot === 'x' ? 'y' : 'x';
	const patch: Partial<CompareConfig> = { [slot]: axis };
	if (axis && current[other]?.field === axis.field) patch[other] = null;
	setCompare(tabId, patch);
}

export function swapAxes(tabId: string): void {
	const current = $state.snapshot(getCompare(tabId)) as CompareConfig;
	setCompare(tabId, { x: current.y, y: current.x });
}

export function clearAxes(tabId: string): void {
	setCompare(tabId, { x: null, y: null });
}

export function turnOffCompare(tabId: string): void {
	setCompare(tabId, { armed: false });
	if (drawerTabId === tabId) drawerTabId = null;
}

export function isQuantityField(tabId: string, field: string): boolean {
	return quantityFieldsOf(getCompareSchema(tabId)).includes(field);
}

export function quantityNoteVisible(_revision: number, tabId: string, field: string): boolean {
	const config = getCompare(tabId);
	return isQuantityField(tabId, field) && config.armed && !blockers[tabId] && cellCount(config) > 0;
}

export function formQuantity(tabId: string, formData: Record<string, unknown>): number {
	const values = quantityFieldsOf(getCompareSchema(tabId)).map((field) => Number(formData[field] ?? 1) || 1);
	return values.length > 0 ? Math.max(...values) : 1;
}

export function readCompareState(_revision: number, tabId: string) {
	return {
		config: getCompare(tabId),
		blocked: compareBlockedReason(tabId),
		gridRunning: isGridRunning(tabId),
		drawerOpen: isCompareDrawerOpen(tabId),
		cells: getActiveGrid(tabId)?.cells ?? []
	};
}

export function readCompareSummary(
	_revision: number,
	tabId: string,
	limitRows: LimitRow[],
	contactLine?: string
): CompareSummary {
	return getCompareSummary(tabId, limitRows, contactLine);
}

export function axisRoleFor(tabId: string, field: string, _revision?: number): { role: 'x' | 'y'; count: number } | null {
	const config = getCompare(tabId);
	if (!config.armed || blockers[tabId]) return null;
	if (config.x?.field === field && config.x.values.length > 0) return { role: 'x', count: config.x.values.length };
	if (config.y?.field === field && config.y.values.length > 0) return { role: 'y', count: config.y.values.length };
	return null;
}

export function isCompareActive(tabId: string): boolean {
	const config = getCompare(tabId);
	return config.armed && !blockers[tabId] && cellCount(config) > 0;
}

export function setCompareBlocked(tabId: string, reason: string | null): void {
	if ((blockers[tabId] ?? null) === reason) return;
	blockers[tabId] = reason;
	touch();
}

export function compareBlockedReason(tabId: string): string | null {
	return blockers[tabId] ?? null;
}

export function openCompareDrawer(tabId: string): void {
	drawerTabId = tabId;
	touch();
	void ensureGridSettings();
}

export function closeCompareDrawer(): void {
	drawerTabId = null;
	touch();
}

export function toggleCompareDrawer(tabId: string): void {
	if (drawerTabId === tabId) closeCompareDrawer();
	else openCompareDrawer(tabId);
}

export function isCompareDrawerOpen(tabId: string): boolean {
	return drawerTabId === tabId;
}

export function armCompare(tabId: string): void {
	setCompare(tabId, { armed: true });
	openCompareDrawer(tabId);
}

export function getGridSettings(): GridSettings {
	return gridSettings;
}

export async function ensureGridSettings(): Promise<GridSettings> {
	if (settingsLoaded) return gridSettings;
	settingsLoaded = true;
	gridSettings = await fetchGridSettings();
	touch();
	return gridSettings;
}

export function getCompareSummary(
	tabId: string,
	limitRows: LimitRow[],
	contactLine: string = DEFAULT_CONTACT_LINE
): CompareSummary {
	return deriveCompareSummary({
		count: cellCount(getCompare(tabId)),
		perCellMs: getCellEstimateMs(tabId),
		hardCap: gridSettings.hard_cap,
		confirmAbove: gridSettings.confirm_above,
		limitRows,
		serverShortfall: refusals[tabId] ?? null,
		contactLine,
		blockedReason: blockers[tabId] ?? null
	});
}

export function getGridRefusal(tabId: string): DailyShortfall | null {
	return refusals[tabId] ?? null;
}

export function clearGridRefusal(tabId: string): void {
	refusals[tabId] = null;
	touch();
}

export function isSubmittingGrid(tabId: string): boolean {
	return submitting[tabId] === true;
}

export function publishCompareSchema(tabId: string, key: string, schema: unknown): void {
	schemas = { ...schemas, [tabId]: { key, schema } };
	touch();
	if (reconciledKeys.get(tabId) === key) return;
	reconciledKeys.set(tabId, key);
	reconcileAxes(tabId);
}

export function getCompareSchema(tabId: string): unknown {
	return schemas[tabId]?.schema ?? null;
}

export function getCandidates(tabId: string, formData?: Record<string, unknown>): AxisCandidate[] {
	const schema = getCompareSchema(tabId);
	if (!schema) return [];
	const data = formData ?? findTab(tabId)?.formData ?? {};
	return buildAxisCandidates(schema, data, { hasPluginEditor: hasAxisEditor });
}

export function reconcileAxes(tabId: string): string[] {
	const config = getCompare(tabId);
	if (!config.x && !config.y) return [];
	const schema = getCompareSchema(tabId);
	if (!schema) return [];
	const known = new Set(
		buildAxisCandidates(schema, findTab(tabId)?.formData ?? {}, { hasPluginEditor: hasAxisEditor }).map(
			(candidate) => candidate.field
		)
	);
	const dropped: CompareAxis[] = [];
	const keep = (axis: CompareAxis | null): CompareAxis | null => {
		if (!axis) return null;
		if (axis.field === PROMPT_AXIS_FIELD || known.has(axis.field)) return axis;
		dropped.push(axis);
		return null;
	};
	const x = keep(config.x);
	const y = keep(config.y);
	if (dropped.length === 0) return [];
	setCompare(tabId, { x, y });
	const names = dropped.map((axis) => axis.label).join(' and ');
	toasts.info(`${names} ${dropped.length === 1 ? 'is' : 'are'} not in this preset, so ${dropped.length === 1 ? 'it was' : 'they were'} removed from the comparison.`);
	return dropped.map((axis) => axis.field);
}

export function getCellEstimateMs(tabId: string): number | null {
	const tab = findTab(tabId);
	if (!tab?.selectedPreset) return null;
	const remembered = cellMs[tab.selectedPreset];
	if (remembered > 0) return remembered;
	const last = tab.generation.lastDurationMs;
	return last && last > 0 ? last : null;
}

function previewGrid(config: CompareConfig): ActiveGrid | null {
	const { cols, rows } = effectiveAxes(config);
	if (!cols) return null;
	const colCount = cols.values.length;
	const rowCount = rows ? rows.values.length : 1;
	const cells: GridCell[] = [];
	for (let y = 0; y < rowCount; y++) {
		for (let x = 0; x < colCount; x++) {
			cells.push(emptyCell(x, y, cellAxisValues(config, x, y)));
		}
	}
	return { id: null, config, cells, cols: colCount, rows: rowCount };
}

export function getActiveGrid(tabId: string): ActiveGrid | null {
	const live = liveGrids[tabId];
	if (live) return live;
	const config = getCompare(tabId);
	if (!config.armed || blockers[tabId]) return null;
	return previewGrid(config);
}

export function readWorkbenchGrid(_revision: number, tabId: string): ActiveGrid | null {
	const grid = getActiveGrid(tabId);
	if (!grid?.id) return grid;
	return getCompare(tabId).armed || isGridRunning(tabId) ? grid : null;
}

export function isGridRunning(tabId: string): boolean {
	const grid = liveGrids[tabId];
	return !!grid?.cells.some((cell) => cell.status === 'queued' || cell.status === 'running');
}

export function failedCellCount(tabId: string): number {
	return liveGrids[tabId]?.cells.filter((cell) => cell.status === 'failed').length ?? 0;
}

function reindex(tabId: string): void {
	for (const [generationId, entry] of generationIndex) {
		if (entry.tabId === tabId) generationIndex.delete(generationId);
	}
	const grid = liveGrids[tabId];
	if (!grid) return;
	grid.cells.forEach((cell, index) => {
		if (cell.generationId) generationIndex.set(cell.generationId, { tabId, index });
	});
}

function setLiveGrid(tabId: string, server: ServerGrid): ActiveGrid {
	const next = gridFromServer(server, liveGrids[tabId]);
	liveGrids[tabId] = next;
	reindex(tabId);
	touch();
	if (findTab(tabId)?.compareGridId !== server.id) tabsStore.updateTab(tabId, { compareGridId: server.id });
	return next;
}

function enrollCells(tabId: string, server: ServerGrid, fresh: boolean): string[] {
	const tab = findTab(tabId);
	if (!tab) return [];
	const outstanding = server.cells.filter(
		(cell) => cell.generation_id && (cell.status === 'queued' || cell.status === 'running')
	);
	const known = new Set((tab.generation.queue ?? []).map((entry) => entry.generation_id));
	const additions = outstanding
		.filter((cell) => !known.has(cell.generation_id as string))
		.map((cell, index) => ({
			generation_id: cell.generation_id as string,
			queue_position: cell.status === 'running' ? null : index,
			status: cell.status === 'running' ? ('running' as const) : ('pending' as const)
		}));
	if (additions.length === 0) return [];
	const first = outstanding[0]?.generation_id as string;
	const owns = tab.activeGenerationId && known.has(tab.activeGenerationId);
	tabsStore.updateTab(tabId, {
		...(owns ? {} : { activeGenerationId: first }),
		generation: {
			...tab.generation,
			isGenerating: true,
			cancelNotice: null,
			cancelledGenerationId: null,
			startedAt: fresh || !tab.generation.startedAt ? Date.now() : tab.generation.startedAt,
			totalTime: null,
			...(owns
				? {}
				: {
						currentGeneration: {
							id: first,
							generation_id: first,
							status: 'running',
							queue_position: null
						}
					}),
			queue: [...(tab.generation.queue ?? []), ...additions]
		}
	});
	return additions.map((entry) => entry.generation_id);
}

export type SubmitGridResult =
	| { ok: true; grid: ActiveGrid; generationIds: string[] }
	| { ok: false; reason: 'no_axes' | 'too_large' | 'refused' | 'error'; message: string; shortfall?: DailyShortfall };

export async function submitGrid(tabId: string, request: GenerationRequest): Promise<SubmitGridResult> {
	const config = getCompare(tabId);
	const { cols, rows } = effectiveAxes(config);
	if (!cols) return { ok: false, reason: 'no_axes', message: 'Pick at least one field to compare.' };
	const count = cellCount(config);
	const settings = await ensureGridSettings();
	if (count > settings.hard_cap) {
		return {
			ok: false,
			reason: 'too_large',
			message: `A comparison can have at most ${settings.hard_cap} cells. This one has ${count}.`
		};
	}
	submitting[tabId] = true;
	refusals[tabId] = null;
	touch();
	try {
		const server = await postGrid({
			request,
			x_axis: cols,
			y_axis: rows,
			lock_seed: effectiveLockSeed(config)
		});
		const grid = setLiveGrid(tabId, server);
		const generationIds = enrollCells(tabId, server, true);
		runHandlers?.enrolled(tabId, generationIds);
		return { ok: true, grid, generationIds };
	} catch (error) {
		const shortfall = shortfallFromError(error);
		if (shortfall || refusalFromError(error)) {
			if (shortfall) refusals[tabId] = shortfall;
			return {
				ok: false,
				reason: 'refused',
				message: shortfall ? `${shortfall.needed} needed, ${shortfall.remaining} left today.` : 'A limit on your account was reached.',
				shortfall: shortfall ?? undefined
			};
		}
		const detail = (error as { response?: { data?: { detail?: unknown; error?: string; message?: string } } })?.response?.data;
		const code = typeof detail?.detail === 'object' && detail?.detail ? (detail.detail as { error?: string }).error : detail?.error;
		if (code === 'grid_too_large') {
			return { ok: false, reason: 'too_large', message: `A comparison can have at most ${settings.hard_cap} cells.` };
		}
		return {
			ok: false,
			reason: 'error',
			message: error instanceof Error && error.message ? error.message : 'Could not start the comparison.'
		};
	} finally {
		submitting[tabId] = false;
		touch();
	}
}

export async function loadGrid(tabId: string, gridId: string): Promise<ActiveGrid | null> {
	try {
		const server = await fetchGrid(gridId);
		return setLiveGrid(tabId, server);
	} catch {
		return null;
	}
}

export async function restoreGrids(): Promise<void> {
	for (const tab of get(tabsStore).tabs) {
		if (!tab.compareGridId || liveGrids[tab.id]?.id === tab.compareGridId) continue;
		const grid = await loadGrid(tab.id, tab.compareGridId);
		if (!grid) tabsStore.updateTab(tab.id, { compareGridId: null });
	}
}

export async function retryFailed(tabId: string): Promise<string[]> {
	const grid = liveGrids[tabId];
	if (!grid?.id) return [];
	try {
		const server = await postRetryFailed(grid.id);
		setLiveGrid(tabId, server);
		const generationIds = enrollCells(tabId, server, false);
		runHandlers?.enrolled(tabId, generationIds);
		return generationIds;
	} catch (error) {
		const shortfall = shortfallFromError(error);
		if (shortfall) refusals[tabId] = shortfall;
		else toasts.error(error instanceof Error && error.message ? error.message : 'Could not retry the failed cells.');
		touch();
		return [];
	}
}

export async function cancelGrid(tabId: string): Promise<string[]> {
	const grid = liveGrids[tabId];
	if (!grid) return [];
	const cancelled = new Set<string>();
	try {
		const response = await api.clearGenerationQueue(tabId);
		for (const id of response.success ? (response.data?.cancelled ?? []) : []) cancelled.add(id);
	} catch {
		toasts.error('Could not cancel the queued cells.');
	}
	for (const cell of grid.cells) {
		if (cell.status !== 'running' || !cell.generationId) continue;
		try {
			await api.cancelGeneration(cell.generationId);
			cancelled.add(cell.generationId);
		} catch {
			continue;
		}
	}
	for (const cell of grid.cells) {
		if (cell.generationId && cancelled.has(cell.generationId)) {
			cell.status = 'cancelled';
			cell.progress = null;
			cell.previewUrl = null;
		}
	}
	touch();
	const ids = [...cancelled];
	runHandlers?.cancelled(tabId, ids);
	return ids;
}

export async function deleteGrid(gridId: string): Promise<void> {
	await removeGrid(gridId);
	for (const tabId of Object.keys(liveGrids)) {
		if (liveGrids[tabId]?.id !== gridId) continue;
		liveGrids[tabId] = null;
		reindex(tabId);
		touch();
		if (findTab(tabId)?.compareGridId === gridId) tabsStore.updateTab(tabId, { compareGridId: null });
	}
}

export function dismissActiveGrid(tabId: string): void {
	liveGrids[tabId] = null;
	reindex(tabId);
	touch();
	if (findTab(tabId)?.compareGridId) tabsStore.updateTab(tabId, { compareGridId: null });
}

function scheduleRefresh(tabId: string): void {
	const grid = liveGrids[tabId];
	if (!grid?.id) return;
	const existing = refreshTimers.get(tabId);
	if (existing) clearTimeout(existing);
	refreshTimers.set(
		tabId,
		setTimeout(() => {
			refreshTimers.delete(tabId);
			const id = liveGrids[tabId]?.id;
			if (id) void loadGrid(tabId, id);
		}, REFRESH_DELAY_MS)
	);
}

function rememberCellDuration(tabId: string, generationId: string): void {
	const started = runStarted.get(generationId);
	runStarted.delete(generationId);
	const presetId = findTab(tabId)?.selectedPreset;
	if (!started || !presetId) return;
	const elapsed = Date.now() - started;
	if (elapsed <= 0) return;
	cellMs = { ...cellMs, [presetId]: Math.round(elapsed) };
	saveCellMs();
	touch();
}

function announceGridFinished(grid: ActiveGrid): void {
	const progress = gridProgress(grid);
	if (progress.active) return;
	if (progress.failed > 0) {
		toasts.warning(`Comparison finished: ${progress.done} of ${progress.total} cells done, ${progress.failed} failed.`);
	} else if (progress.done > 0) {
		toasts.success(`Comparison finished: ${progress.done} of ${progress.total} cells done.`);
	}
}

export function observeGenerationMessage(message: { type: string; [key: string]: unknown }): boolean {
	if (!COMPARE_MESSAGE_TYPES.has(message.type)) return false;
	const generationId = generationIdOf(message);
	if (!generationId) return false;
	const entry = generationIndex.get(generationId);
	if (!entry) {
		const gridId = (message as { grid_id?: string }).grid_id;
		if (gridId) {
			for (const [tabId, grid] of Object.entries(liveGrids)) {
				if (grid?.id === gridId) scheduleRefresh(tabId);
			}
		}
		return false;
	}
	const grid = liveGrids[entry.tabId];
	const cell = grid?.cells[entry.index];
	if (!grid || !cell) return false;
	const next = applyCellMessage(cell, message);
	if (next === cell) return true;
	const wasActive = gridProgress(grid).active;
	if (next.status === 'running' && cell.status !== 'running' && !runStarted.has(generationId)) {
		runStarted.set(generationId, Date.now());
	}
	grid.cells[entry.index] = next;
	touch();
	if (COMPARE_TERMINAL_TYPES.has(message.type)) {
		if (next.status === 'completed') rememberCellDuration(entry.tabId, generationId);
		else runStarted.delete(generationId);
		scheduleRefresh(entry.tabId);
		if (wasActive) announceGridFinished(grid);
	}
	return true;
}

export function ownsGeneration(generationId: string | undefined): boolean {
	return !!generationId && generationIndex.has(generationId);
}

export function isTerminalStatus(status: string): boolean {
	return TERMINAL.has(status);
}

export function resetCompareStoreForTests(): void {
	for (const timer of refreshTimers.values()) clearTimeout(timer);
	refreshTimers.clear();
	generationIndex.clear();
	runStarted.clear();
	reconciledKeys.clear();
	seenConfig.clear();
	for (const key of Object.keys(configs)) delete configs[key];
	for (const key of Object.keys(liveGrids)) delete liveGrids[key];
	for (const key of Object.keys(blockers)) delete blockers[key];
	for (const key of Object.keys(refusals)) delete refusals[key];
	for (const key of Object.keys(submitting)) delete submitting[key];
	drawerTabId = null;
	runHandlers = null;
	schemas = {};
	cellMs = {};
	settingsLoaded = false;
	gridSettings = { ...DEFAULT_GRID_SETTINGS };
	touch();
}
