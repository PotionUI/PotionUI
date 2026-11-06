import type { ToolTabWatchContext } from '$lib/tool-tabs/types';
import { elixirsApi, type ElixirsApi } from '$lib/services/api/elixirs';
import { nodeRunView } from './runs';
import { elixirIdOf } from './ids';
import { elixirTabStatus, isBrewingStatus, type StatusStep } from './tabStatus';

const FAST_MS = 3000;
const SLOW_MS = 15000;
const IDLE_MS = 30000;
const LIVE = new Set(['pending', 'running', 'cancelling', 'awaiting_selection', 'needs_attention']);

interface WatchNode {
	id: string;
	kind?: string;
}

export function watchElixirTab(context: ToolTabWatchContext, client: () => ElixirsApi = elixirsApi): () => void {
	const elixirId = elixirIdOf(context.tab);
	if (!elixirId) return () => {};
	let stopped = false;
	let timer: ReturnType<typeof setTimeout> | null = null;
	let nodes: WatchNode[] | null = null;
	let wasBrewing = isBrewingStatus(context.tab.status);

	const schedule = (delay: number) => {
		if (stopped) return;
		timer = setTimeout(() => void tick(), delay);
	};

	const tick = async () => {
		if (stopped) return;
		const api = client();
		const listed = await api.listRuns(elixirId, { limit: 1 });
		if (stopped) return;
		const latest = listed.ok ? (listed.data?.runs?.[0] ?? null) : null;
		if (!latest || (!LIVE.has(latest.status) && !wasBrewing)) {
			schedule(IDLE_MS);
			return;
		}
		if (!nodes) {
			const elixir = await api.getElixir(elixirId);
			if (stopped) return;
			nodes = elixir.ok ? (elixir.data?.definition?.nodes ?? []) : [];
		}
		const fetched = await api.getRun(latest.id);
		if (stopped || !fetched.ok) {
			schedule(SLOW_MS);
			return;
		}
		const run = fetched.data;
		const steps: StatusStep[] = (nodes ?? [])
			.filter((node) => node.kind !== 'media' && run?.node_states?.[node.id])
			.map((node) => ({ view: nodeRunView(run, node as never) }));
		const status = elixirTabStatus(run, steps, { background: true, wasBrewing });
		if (status || wasBrewing) context.setStatus(status);
		wasBrewing = isBrewingStatus(status);
		schedule(wasBrewing ? FAST_MS : SLOW_MS);
	};

	schedule(0);
	return () => {
		stopped = true;
		if (timer) clearTimeout(timer);
	};
}
