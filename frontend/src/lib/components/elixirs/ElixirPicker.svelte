<script lang="ts">
	import { onMount, tick } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Spinner } from '$lib/components/ui';
	import { tabsStore } from '$lib/stores/tabs';
	import { toasts } from '$lib/stores/toast';
	import { isToolTab, type ToolTabStatus } from '$lib/types/tabs';
	import { elixirsApi } from '$lib/services/api/elixirs';
	import { ELIXIR_TOOL, elixirIdOf, openElixirTab, type ElixirRef } from '$lib/elixirs/tab';
	import { allElixirsOpen } from '$lib/elixirs/events';
	import { newElixirLabel, newElixirName, pickerRows, rowStatus, stepWord, type PickerSummary } from '$lib/elixirs/picker';
	import { relativeTime } from '$lib/elixirs/time';
	import ElixirThumbs from './ElixirThumbs.svelte';
	import StatusDot from './StatusDot.svelte';

	interface Props {
		close: () => void;
		back?: () => boolean;
		phone?: boolean;
		onopen?: (elixir: ElixirRef) => void;
	}

	let { close, back, phone = false, onopen }: Props = $props();

	const TONE_TEXT: Record<string, string> = {
		signal: 'text-signal',
		danger: 'text-danger',
		success: 'text-success',
		warning: 'text-warning',
		neutral: 'text-fg-subtle'
	};
	const listId = `elixir-picker-${Math.random().toString(36).slice(2, 8)}`;

	const client = elixirsApi();

	let elixirs = $state.raw<PickerSummary[] | null>(null);
	let loadError = $state<string | null>(null);
	let query = $state('');
	let highlight = $state(0);
	let creating = $state(false);
	let input = $state<HTMLInputElement>();

	let openTabs = $derived(
		new Map<string, ToolTabStatus | null>(
			$tabsStore.tabs
				.filter(isToolTab)
				.filter((tab) => tab.tool === ELIXIR_TOOL && !!elixirIdOf(tab))
				.map((tab) => [elixirIdOf(tab) as string, tab.status ?? null])
		)
	);
	let rows = $derived(pickerRows(elixirs ?? [], query));
	let total = $derived(elixirs?.length ?? 0);

	onMount(() => {
		void tick().then(() => input?.focus());
		void load();
	});

	async function load() {
		loadError = null;
		const result = await client.listElixirs();
		if (result.ok) elixirs = result.data?.elixirs ?? [];
		else loadError = result.error.message;
	}

	function open(elixir: ElixirRef) {
		close();
		if (onopen) onopen(elixir);
		else openElixirTab(elixir);
	}

	async function create() {
		if (creating) return;
		creating = true;
		const result = await client.createElixir(newElixirName(query));
		creating = false;
		if (!result.ok) {
			toasts.show('error', result.error.message);
			return;
		}
		open({ id: result.data.id, name: result.data.name });
	}

	function showAll() {
		close();
		allElixirsOpen.set(true);
	}

	function oninput() {
		highlight = 0;
	}

	function move(step: number) {
		if (rows.length === 0) return;
		highlight = (highlight + step + rows.length) % rows.length;
		void tick().then(() => document.getElementById(`${listId}-${highlight}`)?.scrollIntoView({ block: 'nearest' }));
	}

	function onkeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' || event.key === 'ArrowUp') {
			event.preventDefault();
			event.stopPropagation();
			move(event.key === 'ArrowDown' ? 1 : -1);
		} else if (event.key === 'Enter') {
			event.preventDefault();
			const row = rows[highlight];
			if (row) open(row);
			else if (elixirs) void create();
		}
	}

	function when(elixir: PickerSummary, live: boolean): string {
		if (live) return 'now';
		return relativeTime(String(elixir.status_at ?? elixir.updated_at ?? ''));
	}
</script>

<div class="picker" class:phone data-testid="elixir-picker">
	{#if phone}
		<p class="px-1 pb-2 text-base font-semibold text-fg">Open an elixir</p>
	{/if}
	<div class="flex items-center gap-1.5 pb-1.5">
		{#if back}
			<Tooltip text="Back" position="bottom" delay={150}>
				<button type="button" class="icon-button" aria-label="Back to new tab menu" onclick={() => back?.()}>
					<Icon name="arrow-left" className="h-4 w-4" />
				</button>
			</Tooltip>
		{/if}
		<label class="search">
			<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
			<input
				bind:this={input}
				bind:value={query}
				type="text"
				role="combobox"
				aria-expanded="true"
				aria-controls={listId}
				aria-activedescendant={rows.length > 0 ? `${listId}-${highlight}` : undefined}
				aria-label="Find an elixir"
				placeholder="Find an elixir"
				autocomplete="off"
				{oninput}
				onkeydowncapture={onkeydown}
			/>
		</label>
	</div>

	<button type="button" class="row" data-testid="elixir-picker-new" disabled={creating} onclick={create}>
		<span class="lead"><Icon name="plus" className="h-4 w-4" /></span>
		<span class="min-w-0 flex-1">
			<span class="block truncate text-sm text-fg">{newElixirLabel(query)}</span>
			<span class="block text-xs text-fg-subtle">An empty elixir, ready for its first step</span>
		</span>
		{#if creating}<Spinner size="sm" />{/if}
	</button>

	<p class="label font-mono text-xs uppercase tracking-wider text-fg-subtle">{query.trim() ? 'Matches' : 'Recent'}</p>

	{#if loadError}
		<p class="px-2.5 py-3 text-sm text-danger" role="alert">{loadError}</p>
	{:else if !elixirs}
		<div class="flex justify-center py-4" role="status" aria-label="Loading elixirs"><Spinner size="sm" /></div>
	{:else if rows.length === 0}
		<p class="px-2.5 py-3 text-sm text-fg-muted">{query.trim() ? `No elixir named “${query.trim()}”.` : 'No elixirs yet.'}</p>
	{:else}
		<div id={listId} role="listbox" aria-label={query.trim() ? 'Matching elixirs' : 'Recent elixirs'}>
			{#each rows as elixir, index (elixir.id)}
				{@const tabStatus = openTabs.get(elixir.id)}
				{@const isOpen = openTabs.has(elixir.id)}
				{@const status = rowStatus(elixir, tabStatus)}
				<button
					type="button"
					id="{listId}-{index}"
					role="option"
					aria-selected={index === highlight}
					class="prow"
					class:hl={index === highlight}
					data-testid="elixir-picker-row"
					data-elixir-id={elixir.id}
					onclick={() => open(elixir)}
					onpointermove={() => (highlight = index)}
				>
					<span class="lead relative">
						<Icon name="flask" className="h-4 w-4" />
						{#if status.live || (isOpen && tabStatus)}
							<span class="corner"><StatusDot tone={status.tone} pulse={status.pulse} ring={status.ring} /></span>
						{/if}
					</span>
					<span class="min-w-0 flex-1">
						<span class="block truncate text-sm font-medium text-fg">{elixir.name}</span>
						<span class="flex gap-1.5 overflow-hidden whitespace-nowrap text-xs text-fg-subtle">
							<span class={TONE_TEXT[status.tone] ?? 'text-fg-subtle'}>{status.label}</span>
							<span aria-hidden="true">·</span>
							<span><span class="font-mono tabular-nums">{elixir.node_count ?? 0}</span> {stepWord(elixir.node_count ?? 0)}</span>
							{#if !phone && when(elixir, status.live)}
								<span aria-hidden="true">·</span>
								<span>{when(elixir, status.live)}</span>
							{/if}
						</span>
					</span>
					{#if isOpen}
						<span class="open-chip" data-testid="elixir-open-chip"><Icon name="external-link" className="h-3 w-3" />Open</span>
					{:else}
						<ElixirThumbs outputs={elixir.latest_outputs ?? []} />
					{/if}
				</button>
			{/each}
		</div>
	{/if}

	<div class="my-1 h-px bg-line" role="separator"></div>
	<button type="button" class="row" data-testid="elixir-picker-all" onclick={showAll}>
		<span class="lead"><Icon name="layers" className="h-4 w-4" /></span>
		<span class="min-w-0 flex-1 text-sm text-fg">All elixirs…</span>
		<span class="font-mono text-xs tabular-nums text-fg-subtle">{total}</span>
	</button>
	<div class="mt-1 flex items-start gap-2 border-t border-line px-2.5 pb-1 pt-2.5 text-xs text-fg-subtle">
		<Icon name="info" className="mt-px h-3.5 w-3.5 flex-shrink-0" />
		<span>An elixir that is already open switches to its tab.</span>
	</div>
</div>

<style>
	.picker {
		display: flex;
		min-width: 0;
		flex-direction: column;
	}

	.icon-button {
		display: flex;
		height: 2rem;
		width: 2rem;
		flex-shrink: 0;
		align-items: center;
		justify-content: center;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
	}

	.icon-button:hover,
	.icon-button:focus-visible {
		background: rgb(var(--surface-3));
		color: rgb(var(--fg));
		outline: none;
	}

	.search {
		display: flex;
		height: 2rem;
		min-width: 0;
		flex: 1;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.625rem;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
	}

	.phone .search {
		height: 2.5rem;
	}

	.search:focus-within {
		border-color: rgb(var(--line-hover));
		box-shadow: 0 0 0 1px rgb(var(--accent) / 0.5);
	}

	.search input {
		min-width: 0;
		flex: 1;
		border: 0;
		background: none;
		color: rgb(var(--fg));
		font-size: 0.875rem;
		outline: none;
	}

	.search input::placeholder {
		color: rgb(var(--fg-subtle));
	}

	.row,
	.prow {
		display: flex;
		width: 100%;
		align-items: center;
		gap: 0.625rem;
		border-radius: 4px;
		padding: 0.375rem 0.625rem;
		text-align: left;
	}

	.prow {
		min-height: 50px;
	}

	.phone .prow {
		min-height: 56px;
	}

	.row:hover,
	.row:focus-visible,
	.prow.hl,
	.prow:focus-visible {
		background: rgb(var(--surface-3));
		outline: none;
	}

	.lead {
		display: grid;
		width: 30px;
		height: 30px;
		flex-shrink: 0;
		place-items: center;
		border-radius: 4px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg-muted));
	}

	.row .lead {
		background: none;
	}

	.corner {
		position: absolute;
		top: -3px;
		right: -3px;
		display: flex;
		border-radius: 50%;
		box-shadow: 0 0 0 2px rgb(var(--surface-2));
	}

	.label {
		padding: 0.5rem 0.625rem 0.25rem;
	}

	.open-chip {
		display: inline-flex;
		height: 20px;
		flex-shrink: 0;
		align-items: center;
		gap: 4px;
		padding: 0 6px;
		border-radius: 4px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg-muted));
		font-size: 12px;
	}
</style>
