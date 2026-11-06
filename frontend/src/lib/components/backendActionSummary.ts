import type { BackendQuickAction } from '$lib/services/admin-api';

export type SummaryPart = { plain: string } | { num: string };
export type ResultSummary = { lines: SummaryPart[][]; keys?: string[] };

export function resultSummaryFor(
	action: BackendQuickAction,
	data: Record<string, unknown> | null
): ResultSummary {
	if (!data) return { lines: [[{ plain: 'Completed' }]] };

	if (action.id === 'clear-vram') {
		const offloaded = Number(data.offloaded_count ?? 0);
		const unloaded = Number(data.unloaded_count ?? 0);
		const freedGb = Number(data.freed_gb ?? 0);
		if (offloaded + unloaded === 0) {
			return {
				lines: [
					[{ plain: 'Nothing was GPU-resident — VRAM was already clear.' }],
					[
						{
							plain: "Models cached in RAM are unaffected; use 'Clear VRAM & Cache (RAM)' to drop those."
						}
					]
				]
			};
		}
		const line: SummaryPart[] = [
			{ plain: 'Offloaded ' },
			{ num: `${offloaded}` },
			{ plain: ` model${offloaded === 1 ? '' : 's'} to RAM` }
		];
		if (unloaded > 0) line.push({ plain: ', unloaded ' }, { num: `${unloaded}` });
		line.push({ plain: ', freed ' }, { num: `${freedGb.toFixed(2)} GB` }, { plain: ' of VRAM' });
		return { lines: [line] };
	}

	if (action.id === 'clear-cache') {
		const keys = Array.isArray(data.cache_keys_cleared)
			? (data.cache_keys_cleared as unknown[]).map(String)
			: [];
		return {
			lines: [
				[
					{ plain: 'Evicted ' },
					{ num: `${keys.length}` },
					{ plain: ` cached model${keys.length === 1 ? '' : 's'}, RAM returned to the OS` }
				]
			],
			keys: keys.length > 0 && keys.length <= 8 ? keys : undefined
		};
	}

	const lines: SummaryPart[][] = [];
	if (typeof data.message === 'string' && data.message) lines.push([{ plain: data.message }]);
	if (typeof data.freed_gb === 'number') {
		lines.push([{ plain: 'Freed ' }, { num: `${(data.freed_gb as number).toFixed(2)} GB` }]);
	}
	if (typeof data.offloaded_count === 'number') {
		lines.push([{ plain: 'Offloaded ' }, { num: `${data.offloaded_count}` }, { plain: ' item(s)' }]);
	}
	return { lines: lines.length > 0 ? lines : [[{ plain: 'Completed' }]] };
}
