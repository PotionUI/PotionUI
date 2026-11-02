import { describe, it, expect } from 'vitest';
import { resultSummaryFor, type SummaryPart } from './backendActionSummary';

const clearVram = {
	id: 'clear-vram',
	label: 'Clear VRAM',
	icon: '',
	endpoint: '/api/backends/native-1/actions/clear-vram',
	method: 'POST'
};

const text = (line: SummaryPart[]) => line.map((part) => ('num' in part ? part.num : part.plain)).join('');

describe('clear-vram result summary', () => {
	it('names offloaded and unloaded models', () => {
		const summary = resultSummaryFor(clearVram, { offloaded_count: 2, unloaded_count: 1, freed_gb: 30.5 });
		expect(summary.lines.map(text)).toEqual(['Offloaded 2 models to RAM, unloaded 1, freed 30.50 GB of VRAM']);
	});

	it('does not call an unload-only run a no-op', () => {
		const summary = resultSummaryFor(clearVram, { offloaded_count: 0, unloaded_count: 2, freed_gb: 24 });
		expect(summary.lines.map(text)).toEqual(['Offloaded 0 models to RAM, unloaded 2, freed 24.00 GB of VRAM']);
	});

	it('leaves the unload count out when nothing was unloaded', () => {
		const summary = resultSummaryFor(clearVram, { offloaded_count: 1, unloaded_count: 0, freed_gb: 4 });
		expect(summary.lines.map(text)).toEqual(['Offloaded 1 model to RAM, freed 4.00 GB of VRAM']);
	});

	it('reports a clear GPU when nothing moved', () => {
		const summary = resultSummaryFor(clearVram, { offloaded_count: 0, unloaded_count: 0, freed_gb: 0 });
		expect(text(summary.lines[0])).toBe('Nothing was GPU-resident — VRAM was already clear.');
	});
});
