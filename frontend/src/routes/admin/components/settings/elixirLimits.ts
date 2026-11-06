export interface ElixirLimit {
	key: string;
	label: string;
	description: string;
	fallback: number;
}

export const ELIXIR_LIMITS: ElixirLimit[] = [
	{ key: 'elixirs_max_nodes', label: 'Steps per elixir', description: 'Preset, file and writer steps in one elixir.', fallback: 24 },
	{ key: 'elixirs_max_edges', label: 'Connections per elixir', description: 'Links between steps in one elixir.', fallback: 48 },
	{ key: 'elixirs_max_branches', label: 'Branches per step', description: 'Steps that can follow one step.', fallback: 8 },
	{ key: 'elixirs_max_media_per_source', label: 'Files per file step', description: 'Files one file step can hold.', fallback: 1000 },
	{ key: 'elixirs_max_outputs_per_connection', label: 'Files per connection', description: 'Files one connection passes on per brew.', fallback: 1000 },
	{ key: 'elixirs_max_invocations', label: 'Generations per brew', description: 'Generations one brew can start in total.', fallback: 200 }
];

export function limitValue(raw: unknown, fallback: number): number {
	const value = Number(raw);
	return Number.isFinite(value) && value >= 1 ? Math.round(value) : fallback;
}

export function parseLimit(input: string): number | null {
	const value = Math.round(Number(input));
	return Number.isFinite(value) && value >= 1 ? value : null;
}
