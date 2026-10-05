import { effectiveAxes } from './axisValues';
import type { ActiveGrid, CompareConfig, GridCell, ServerGrid } from './types';

export function cellAxisValues(config: CompareConfig, x: number, y: number): Record<string, string> {
	const { cols, rows } = effectiveAxes(config);
	const out: Record<string, string> = {};
	if (cols) out[cols.field] = cols.values[x]?.label ?? '';
	if (rows) out[rows.field] = rows.values[y]?.label ?? '';
	return out;
}

export function emptyCell(x: number, y: number, axisValues: Record<string, string> = {}): GridCell {
	return {
		x,
		y,
		generationId: null,
		status: 'empty',
		axisValues,
		seed: null,
		thumbnailUrl: null,
		mediaType: null,
		error: null,
		elapsedSeconds: null,
		progress: null,
		previewUrl: null
	};
}

export function gridFromServer(server: ServerGrid, previous?: ActiveGrid | null): ActiveGrid {
	const config: CompareConfig = {
		armed: true,
		x: server.x_axis,
		y: server.y_axis,
		lockSeed: server.lock_seed
	};
	const cols = Math.max(1, server.x_axis.values.length);
	const rows = server.y_axis ? Math.max(1, server.y_axis.values.length) : 1;
	const byPosition = new Map(server.cells.map((cell) => [`${cell.x}:${cell.y}`, cell]));
	const carried = new Map((previous?.cells ?? []).filter((c) => c.generationId).map((c) => [c.generationId, c]));
	const cells: GridCell[] = [];
	for (let y = 0; y < rows; y++) {
		for (let x = 0; x < cols; x++) {
			const row = byPosition.get(`${x}:${y}`);
			if (!row) {
				cells.push(emptyCell(x, y, cellAxisValues(config, x, y)));
				continue;
			}
			const before = row.generation_id ? carried.get(row.generation_id) : undefined;
			const live = row.status === 'queued' || row.status === 'running';
			cells.push({
				x,
				y,
				generationId: row.generation_id,
				status: row.status,
				axisValues: row.axis_values ?? {},
				seed: row.seed ?? null,
				thumbnailUrl: row.thumbnail_url ?? before?.thumbnailUrl ?? null,
				mediaType: row.media_type ?? before?.mediaType ?? null,
				error: row.error ?? null,
				elapsedSeconds: row.elapsed_seconds ?? null,
				progress: live ? (before?.progress ?? null) : null,
				previewUrl: live ? (before?.previewUrl ?? null) : null
			});
		}
	}
	return { id: server.id, config, cells, cols, rows };
}
