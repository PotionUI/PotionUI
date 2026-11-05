<script lang="ts">
	import { onDestroy, onMount, untrack } from 'svelte';
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import ConfirmModal from '$lib/components/modals/ConfirmModal.svelte';
	import CustomSelect from '$lib/components/CustomSelect.svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { Button, IconButton, Spinner, EmptyState, LoadErrorState, Pagination } from '$lib/components/ui';
	import StatusDot from '$lib/components/elixirs/StatusDot.svelte';
	import { relativeTime } from '$lib/elixirs/time';
	import { ACTIVE_RUN_STATUSES, runElapsed, runLabel } from '$lib/elixirs/runs';
	import { elixirsApi } from '$lib/services/api/elixirs';
	import { toasts } from '$lib/stores/toast';
	import type { BrewRun, RunQuery } from '$lib/elixirs/types';

	interface Props {
		elixirId: string;
		elixirName: string;
		onclose: () => void;
		onopen: (run: BrewRun) => void;
		onbrew?: () => void;
		onchange?: () => void;
		initialQuery?: string;
	}

	let { elixirId, elixirName, onclose, onopen, onbrew, onchange, initialQuery = '' }: Props = $props();

	const client = elixirsApi();

	const PAGE = 50;
	const STATUS_FILTERS = [
		{ id: 'all', label: 'All' },
		{ id: 'active', label: 'Active' },
		{ id: 'finished', label: 'Finished' },
		{ id: 'failed', label: 'Failed' },
		{ id: 'cancelled', label: 'Cancelled' }
	];
	const RANGES = [
		{ id: 'any', label: 'Any time' },
		{ id: 'today', label: 'Today' },
		{ id: '7d', label: '7 days' },
		{ id: '30d', label: '30 days' },
		{ id: 'custom', label: 'Custom' }
	];
	const MODES = [
		{ value: 'any', label: 'Any mode' },
		{ value: 'review', label: 'Choose outputs' },
		{ value: 'automatic', label: 'Runs all branches' }
	];
	const SCOPES = [
		{ value: 'any', label: 'Whole elixir or step' },
		{ value: 'elixir', label: 'Whole elixir' },
		{ value: 'node', label: 'Single step' }
	];
	const TONE_TEXT: Record<string, string> = {
		signal: 'text-signal',
		danger: 'text-danger',
		success: 'text-success',
		warning: 'text-warning',
		neutral: 'text-fg-subtle'
	};

	function style(run: BrewRun) {
		switch (run.status) {
			case 'running':
			case 'pending':
				return { tone: 'signal', pulse: true, ring: false };
			case 'awaiting_selection':
				return { tone: 'signal', pulse: false, ring: true };
			case 'cancelling':
				return { tone: 'warning', pulse: true, ring: false };
			case 'needs_attention':
			case 'failed':
			case 'interrupted':
				return { tone: 'danger', pulse: false, ring: false };
			case 'completed':
				return { tone: 'success', pulse: false, ring: false };
			default:
				return { tone: 'warning', pulse: false, ring: false };
		}
	}
	const isActive = (run: BrewRun) => ACTIVE_RUN_STATUSES.includes(run.status);
	const canStop = (run: BrewRun) => isActive(run) && run.status !== 'cancelling';
	const nameOf = (run: BrewRun) => `${run.scope === 'node' ? 'Node run' : 'Brew'} ${run.number}`;
	const modeLabel = (mode: string) => (mode === 'automatic' ? 'Runs all branches' : 'Choose outputs');
	const plural = (n: number, one: string, many = `${one}s`) => `${n} ${n === 1 ? one : many}`;
	const iso = (date: Date) => `${date.getFullYear()}-${String(date.getMonth() + 1).padStart(2, '0')}-${String(date.getDate()).padStart(2, '0')}`;
	const absolute = (value: string) =>
		new Date(value).toLocaleString('en-GB', { day: 'numeric', month: 'short', year: 'numeric', hour: '2-digit', minute: '2-digit', second: '2-digit' });

	let status = $state('all');
	let range = $state('any');
	let from = $state('');
	let to = $state('');
	let mode = $state('any');
	let scope = $state('any');
	let search = $state(untrack(() => initialQuery));
	let term = $state(untrack(() => initialQuery));

	let runs = $state.raw<BrewRun[]>([]);
	let total = $state(0);
	let active = $state(0);
	let currentPage = $state(1);
	let phase = $state<'loading' | 'ready' | 'error'>('loading');
	let errorMessage = $state('');
	let refreshing = $state(false);
	let selected = $state<string[]>([]);
	let confirm = $state<null | { kind: 'selected' | 'all' }>(null);
	let stopping = $state(false);
	let filtersOpen = $state(false);
	let phone = $state(false);
	let now = $state(Date.now());
	let sequence = 0;
	let list = $state<HTMLDivElement>();
	let searchTimer: ReturnType<typeof setTimeout> | undefined;

	let secondary = $derived((range !== 'any' ? 1 : 0) + (mode !== 'any' ? 1 : 0) + (scope !== 'any' ? 1 : 0));
	let filtered = $derived(status !== 'all' || secondary > 0 || term.trim() !== '');
	let params = $derived.by<RunQuery>(() => {
		const today = new Date();
		const back = (days: number) => {
			const date = new Date(today);
			date.setDate(date.getDate() - days);
			return iso(date);
		};
		const bounds =
			range === 'today' ? { from: iso(today), to: iso(today) }
			: range === '7d' ? { from: back(6) }
			: range === '30d' ? { from: back(29) }
			: range === 'custom' ? { from: from || undefined, to: to || undefined }
			: {};
		return {
			status: status === 'all' ? undefined : status,
			run_mode: mode === 'any' ? undefined : mode,
			scope: scope === 'any' ? undefined : scope,
			q: term.trim() || undefined,
			...bounds
		};
	});
	let selectedRuns = $derived(runs.filter((run) => selected.includes(run.id)));
	let stoppable = $derived(selectedRuns.filter(canStop));
	let totalPages = $derived(Math.max(1, Math.ceil(total / PAGE)));
	let firstShown = $derived((currentPage - 1) * PAGE + 1);
	let lastShown = $derived(Math.min(currentPage * PAGE, total));
	let allSelected = $derived(runs.length > 0 && selected.length === runs.length);
	let anyActiveShown = $derived(runs.some(isActive));

	onMount(() => {
		const query = window.matchMedia('(max-width: 767px)');
		const sync = () => (phone = query.matches);
		sync();
		query.addEventListener('change', sync);
		return () => query.removeEventListener('change', sync);
	});
	onDestroy(() => clearTimeout(searchTimer));

	$effect(() => {
		const value = search;
		clearTimeout(searchTimer);
		searchTimer = setTimeout(() => (term = value), value ? 250 : 0);
	});

	$effect(() => {
		void params;
		untrack(() => {
			selected = [];
			currentPage = 1;
			void reload();
		});
	});

	$effect(() => {
		if (!anyActiveShown) return;
		now = Date.now();
		const tick = setInterval(() => (now = Date.now()), 1000);
		const poll = setInterval(() => void refresh(), 3000);
		return () => {
			clearInterval(tick);
			clearInterval(poll);
		};
	});

	async function reload() {
		const mine = ++sequence;
		if (phase === 'ready') refreshing = true;
		try {
			const result = await client.listRuns(elixirId, { ...params, limit: PAGE, offset: (currentPage - 1) * PAGE });
			if (mine !== sequence) return;
			if (!result.ok) {
				errorMessage = result.error.message;
				phase = 'error';
				return;
			}
			runs = result.data.runs;
			total = result.data.total;
			active = result.data.active;
			phase = 'ready';
		} finally {
			if (mine === sequence) refreshing = false;
		}
	}

	async function refresh() {
		if (phase !== 'ready') return;
		const mine = sequence;
		const result = await client.listRuns(elixirId, { ...params, limit: PAGE, offset: (currentPage - 1) * PAGE });
		if (mine !== sequence || !result.ok) return;
		runs = result.data.runs;
		total = result.data.total;
		active = result.data.active;
		selected = selected.filter((id) => result.data.runs.some((run) => run.id === id));
	}

	function goto(page: number) {
		currentPage = page;
		selected = [];
		void reload().then(() => list?.scrollTo({ top: 0 }));
	}

	function clearFilters() {
		status = 'all';
		range = 'any';
		from = '';
		to = '';
		mode = 'any';
		scope = 'any';
		search = '';
		term = '';
	}

	function toggle(id: string) {
		selected = selected.includes(id) ? selected.filter((item) => item !== id) : [...selected, id];
	}

	function toggleAll() {
		selected = allSelected ? [] : runs.map((run) => run.id);
	}

	async function stop(ids: string[] | 'all') {
		stopping = true;
		try {
			const result = await client.cancelRuns(elixirId, ids === 'all' ? { all_active: true } : { run_ids: ids });
			if (!result.ok) toasts.error(result.error.message);
			selected = [];
			await refresh();
			onchange?.();
		} finally {
			stopping = false;
			confirm = null;
		}
	}

	function stopOne(run: BrewRun) {
		void stop([run.id]);
	}

	function stopSelected() {
		if (stoppable.length > 1) confirm = { kind: 'selected' };
		else if (stoppable.length === 1) void stop([stoppable[0].id]);
	}

	let confirmCopy = $derived.by(() => {
		if (!confirm) return { title: '', message: '', label: '' };
		if (confirm.kind === 'all') {
			const hidden = filtered ? ' This includes brews your filters are hiding.' : '';
			return {
				title: `Stop all ${active} running brews?`,
				message: `Every active brew of “${elixirName}” stops, including ones waiting for your choice or needing attention. Outputs they already made are kept.${hidden}`,
				label: `Stop ${active} brews`
			};
		}
		const skipped = selectedRuns.length - stoppable.length;
		const names = stoppable.map(nameOf);
		const list = names.length <= 3 ? names.join(', ') : `${names.slice(0, 2).join(', ')} and ${names.length - 2} more`;
		return {
			title: `Stop ${stoppable.length} brews?`,
			message: `${list} will stop. Outputs they already made are kept.${skipped > 0 ? ` ${plural(skipped, 'selected brew is', 'selected brews are')} not running and stays as it is.` : ''}`,
			label: `Stop ${stoppable.length} brews`
		};
	});

	const resultText = $derived(filtered ? `${plural(total, 'brew')} match` : plural(total, 'brew'));
</script>

{#snippet picker(label: string, value: string, options: { value: string; label: string }[], onpick: (value: string) => void)}
	<div class="w-[11.5rem] flex-shrink-0">
	<CustomSelect {value} {options} size="sm" menuAlign="left" menuMinWidth={200} on:change={(event) => onpick(event.detail)}>
		{#snippet trigger({ open, selected: current, toggle: flip, keydown })}
			<button
				type="button"
				class="pick"
				class:set={value !== 'any'}
				aria-label={label}
				aria-haspopup="listbox"
				aria-expanded={open}
				onclick={flip}
				onkeydown={keydown}
			>
				<span class="truncate">{current?.label}</span>
				<Icon name="chevron-down" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
			</button>
		{/snippet}
	</CustomSelect>
	</div>
{/snippet}

{#snippet segment(label: string, items: { id: string; label: string }[], value: string, onpick: (id: string) => void, extra?: Record<string, number>)}
	<div class="seg" role="radiogroup" aria-label={label}>
		{#each items as option (option.id)}
			<button type="button" role="radio" aria-checked={value === option.id} onclick={() => onpick(option.id)}>
				{option.label}{#if extra?.[option.id]}<span class="ml-1 font-mono tabular-nums {value === option.id ? 'text-signal' : 'text-fg-subtle'}">{extra[option.id]}</span>{/if}
			</button>
		{/each}
	</div>
{/snippet}

{#snippet dates()}
	{#if range === 'custom'}
		<div class="flex items-center gap-1.5" data-testid="custom-range">
			<label class="date"><span class="sr-only">From date</span><input type="date" bind:value={from} max={to || undefined} aria-label="From date" /></label>
			<span class="text-sm text-fg-subtle" aria-hidden="true">to</span>
			<label class="date"><span class="sr-only">To date</span><input type="date" bind:value={to} min={from || undefined} aria-label="To date" /></label>
		</div>
	{/if}
{/snippet}

<BaseModal
	isOpen
	title="Brew history — {elixirName}"
	sizeClass="md:w-[min(960px,calc(100vw-64px))] md:max-w-none md:h-[min(780px,90vh)]"
	on:close={onclose}
>
	<div class="flex h-full min-h-0 flex-1 flex-col" data-testid="brew-history">
		<div class="tools flex flex-wrap items-center gap-2.5 border-b border-line px-3 py-3 md:px-4">
			<label class="search">
				<Icon name="search" className="h-3.5 w-3.5 flex-shrink-0 text-fg-subtle" />
				<input bind:value={search} type="text" aria-label="Search brews" placeholder="Search brew number or step" autocomplete="off" />
				{#if search}
					<button type="button" class="clear" aria-label="Clear search" onclick={() => (search = '')}><Icon name="close" className="h-3 w-3" /></button>
				{/if}
			</label>
			{#if phone}
				<span class="flex-1"></span>
				<button type="button" class="filters-btn" class:set={secondary > 0} aria-haspopup="dialog" onclick={() => (filtersOpen = true)}>
					<Icon name="sliders" className="h-4 w-4" />
					Filters
					{#if secondary > 0}<span class="font-mono tabular-nums">{secondary}</span>{/if}
				</button>
				<div class="w-full">{@render segment('Status', STATUS_FILTERS, status, (id) => (status = id), { active })}</div>
			{:else}
				{@render segment('Status', STATUS_FILTERS, status, (id) => (status = id), { active })}
				<div class="basis-full flex flex-wrap items-center gap-2.5">
					{@render segment('Started', RANGES, range, (id) => (range = id))}
					{@render dates()}
					{@render picker('Run mode', mode, MODES, (value) => (mode = value))}
					{@render picker('Scope', scope, SCOPES, (value) => (scope = value))}
					{#if filtered}
						<Button variant="ghost" size="sm" icon="close" onclick={clearFilters}>Clear filters</Button>
					{/if}
				</div>
			{/if}
		</div>

		<div class="bar flex min-h-[44px] flex-wrap items-center gap-x-3 gap-y-1 border-b border-line px-3 py-2 md:px-4" data-testid="result-bar" aria-live="polite">
			{#if selected.length > 0}
				<span class="text-sm text-fg"><span class="font-mono tabular-nums">{selected.length}</span> selected</span>
				<span class="min-w-0 flex-1 text-sm text-fg-muted" data-testid="stop-note">
					{#if stoppable.length > 0}
						<span class="font-mono tabular-nums">{stoppable.length}</span> can be stopped. Only running brews can.
					{:else}
						None of these are running, so there is nothing to stop.
					{/if}
				</span>
				<Button variant="ghost" size="sm" onclick={() => (selected = [])}>Clear</Button>
				<Tooltip text={stoppable.length === 0 ? 'Select a running brew to stop it' : 'Stops the running brews you selected'}>
					<Button variant="danger" size="sm" icon="close" disabled={stoppable.length === 0 || stopping} loading={stopping} onclick={stopSelected}>
						Stop {stoppable.length > 0 ? stoppable.length : ''}
					</Button>
				</Tooltip>
			{:else}
				<span class="min-w-0 flex-1 text-sm text-fg-muted" data-testid="result-count">
					{#if phase === 'ready'}
						<span class="font-mono tabular-nums text-fg">{total}</span> {total === 1 ? (filtered ? 'brew matches' : 'brew') : filtered ? 'brews match' : 'brews'}
						{#if active > 0}<span class="px-1 text-fg-subtle" aria-hidden="true">·</span><span class="text-signal"><span class="font-mono tabular-nums">{active}</span> active</span>{/if}
						{#if refreshing}<span class="ml-2 inline-block align-middle"><Spinner size="sm" /></span>{/if}
					{:else}
						<span class="text-fg-subtle">Loading brews</span>
					{/if}
				</span>
				{#if active > 0 && phase === 'ready'}
					<Tooltip text="Stops every active brew of this elixir, even ones your filters hide">
						<Button variant="secondary" size="sm" icon="close" class="!text-danger" onclick={() => (confirm = { kind: 'all' })}>
							Stop all running <span class="font-mono tabular-nums">({active})</span>
						</Button>
					</Tooltip>
				{/if}
			{/if}
		</div>

		<div bind:this={list} class="list min-h-0 flex-1 overflow-y-auto" role="table" aria-label="Brews" aria-busy={phase === 'loading' || refreshing}>
			{#if phase === 'error'}
				<LoadErrorState message={errorMessage} onRetry={() => { phase = 'loading'; void reload(); }} />
			{:else if phase === 'loading'}
				<div class="flex justify-center py-12" role="status" aria-label="Loading brews"><Spinner /></div>
			{:else if runs.length === 0}
				<div class="p-4">
					{#if filtered}
						<EmptyState title="No brews match" description="Nothing in this elixir fits these filters. Try a wider date range, or clear them to see every brew." icon="search" compact>
							{#snippet actions()}
								<Button variant="secondary" size="sm" icon="close" onclick={clearFilters}>Clear filters</Button>
							{/snippet}
						</EmptyState>
					{:else}
						<EmptyState title="No brews yet" description="Brew this elixir and every run lands here, with its status and outputs." icon="flask" compact>
							{#snippet actions()}
								<Button variant="primary" size="sm" icon="play" onclick={onbrew}>Brew</Button>
							{/snippet}
						</EmptyState>
					{/if}
				</div>
			{:else}
				<div class="mrow head" role="row">
					<div role="columnheader"><label class="chk">
						<input
							type="checkbox"
							class="h-4 w-4 accent-signal"
							aria-label="Select all {runs.length} loaded brews"
							checked={allSelected}
							indeterminate={selected.length > 0 && !allSelected}
							onchange={toggleAll}
						/>
					</label></div>
					<span class="head-cell" role="columnheader">Brew</span>
					<span class="head-cell col-status" role="columnheader">Status</span>
					<span class="head-cell col-started" role="columnheader">Started</span>
					<span class="head-cell col-duration num" role="columnheader">Duration</span>
					<span class="head-cell col-steps num" role="columnheader">Steps</span>
					<span class="head-cell col-outputs num" role="columnheader">Outputs</span>
					<span role="columnheader"><span class="sr-only">Actions</span></span>
				</div>
				<div class:dim={refreshing}>
					{#each runs as run (run.id)}
						{@const look = style(run)}
						{@const checked = selected.includes(run.id)}
						<div class="mrow" class:sel={checked} class:stopping={run.status === 'cancelling'} role="row" data-testid="brew-row" data-run-id={run.id} data-number={run.number} data-status={run.status} data-mode={run.run_mode} data-scope={run.scope} data-created={run.created_at}>
							<div role="cell"><label class="chk">
								<input type="checkbox" class="h-4 w-4 accent-signal" aria-label="Select {nameOf(run)}" {checked} onchange={() => toggle(run.id)} />
							</label></div>
							<div class="min-w-0" role="cell">
								<div class="flex min-w-0 items-center gap-2">
									<button type="button" class="open truncate text-sm font-medium text-fg" aria-label="Open {nameOf(run)}" onclick={() => onopen(run)}>{nameOf(run)}</button>
									{#if run.scope === 'node'}
										<span class="chip truncate" data-testid="scope-chip">Step · {run.scope_label}</span>
									{/if}
								</div>
								<div class="sub mb-0.5 flex min-w-0 flex-wrap items-center gap-x-1.5 text-[12px]">
									<span class="inline-flex items-center gap-1.5 {TONE_TEXT[look.tone]}"><StatusDot tone={look.tone} pulse={look.pulse} ring={look.ring} />{runLabel(run)}</span>
									<span class="text-fg-subtle" aria-hidden="true">·</span>
									<span class="text-fg-muted">{relativeTime(run.created_at)}</span>
									<span class="text-fg-subtle" aria-hidden="true">·</span>
									<span class="font-mono tabular-nums text-fg-muted">{runElapsed(run, now)}</span>
								</div>
								<div class="text-[12px] text-fg-subtle md:truncate">
									<span class="hide-md">{modeLabel(run.run_mode)}</span>
									<span class="show-md"><span class="font-mono tabular-nums">{run.steps_done}/{run.steps_total}</span> steps<span class="px-1" aria-hidden="true">·</span><span class="font-mono tabular-nums">{run.outputs}</span> outputs<span class="px-1" aria-hidden="true">·</span>{modeLabel(run.run_mode)}</span>
								</div>
							</div>
							<div class="col-status flex min-w-0 items-center gap-1.5 text-sm {TONE_TEXT[look.tone]}" role="cell">
								<StatusDot tone={look.tone} pulse={look.pulse} ring={look.ring} />
								{#if run.error && !isActive(run)}
									<Tooltip text={run.error} wrapperClass="min-w-0 inline-flex"><span class="truncate">{runLabel(run)}</span></Tooltip>
								{:else}
									<span class="truncate">{runLabel(run)}</span>
								{/if}
							</div>
							<div class="col-started text-sm text-fg-muted" role="cell">
								<Tooltip text={absolute(run.created_at)}><span class="rel">{relativeTime(run.created_at)}</span></Tooltip>
							</div>
							<div class="col-duration num font-mono text-sm tabular-nums text-fg-muted" role="cell">{runElapsed(run, now)}</div>
							<div class="col-steps num font-mono text-sm tabular-nums {run.steps_done < run.steps_total && !isActive(run) ? 'text-fg-muted' : 'text-fg-muted'}" role="cell">{run.steps_done}/{run.steps_total}</div>
							<div class="col-outputs num font-mono text-sm tabular-nums text-fg-muted" role="cell">{run.outputs}</div>
							<div class="act flex items-center justify-end" role="cell">
								{#if run.status === 'cancelling'}
									<Tooltip text="Stopping"><span class="grid h-8 w-8 place-items-center" role="status" aria-label="Stopping {nameOf(run)}"><Spinner size="sm" /></span></Tooltip>
								{:else if canStop(run)}
									<IconButton icon="close" label="Stop brew" size="sm" class="!text-danger" aria-label="Stop {nameOf(run)}" onclick={() => stopOne(run)} />
								{/if}
							</div>
						</div>
					{/each}
				</div>
			{/if}
		</div>

		{#if phase === 'ready' && total > 0}
			<div class="border-t border-line px-3 py-3 md:px-4" data-testid="pager">
				<div class="flex flex-col items-center gap-2 md:flex-row md:justify-between">
					<span class="font-mono text-sm tabular-nums text-fg-muted" data-testid="paging">
						{#if totalPages > 1}Showing {firstShown}-{lastShown} of {total}{:else}{total} {total === 1 ? 'brew' : 'brews'}{/if}
					</span>
					<Pagination {currentPage} {totalPages} size="sm" onPageChange={goto} />
				</div>
			</div>
		{/if}

		<div class="flex items-start gap-2.5 border-t border-line px-4 py-3 text-[12px] text-fg-subtle">
			<Icon name="info" className="mt-px h-3.5 w-3.5 flex-shrink-0" />
			<span>Stopping a brew keeps the outputs it already made. Deleting a generation is blocked while a running brew still needs it.</span>
		</div>
	</div>
</BaseModal>

{#if filtersOpen}
	<BaseModal isOpen compact title="Filters" on:close={() => (filtersOpen = false)}>
		<div class="flex flex-col gap-4 p-4" data-testid="filters-sheet">
			<div>
				<p class="label">Started</p>
				<div class="flex flex-wrap gap-2">
					{#each RANGES as option (option.id)}
						<button type="button" class="opt" aria-pressed={range === option.id} onclick={() => (range = option.id)}>{option.label}</button>
					{/each}
				</div>
				{#if range === 'custom'}
					<div class="mt-2.5">{@render dates()}</div>
				{/if}
			</div>
			<div>
				<p class="label">Run mode</p>
				<div class="flex flex-wrap gap-2">
					{#each MODES as option (option.value)}
						<button type="button" class="opt" aria-pressed={mode === option.value} onclick={() => (mode = option.value)}>{option.label}</button>
					{/each}
				</div>
			</div>
			<div>
				<p class="label">Scope</p>
				<div class="flex flex-wrap gap-2">
					{#each SCOPES as option (option.value)}
						<button type="button" class="opt" aria-pressed={scope === option.value} onclick={() => (scope = option.value)}>{option.label.replace('Whole elixir or step', 'Any')}</button>
					{/each}
				</div>
			</div>
		</div>
		<div class="flex items-center gap-2 border-t border-line p-3">
			<Button variant="ghost" size="sm" disabled={secondary === 0} onclick={() => { range = 'any'; from = ''; to = ''; mode = 'any'; scope = 'any'; }}>Clear</Button>
			<span class="flex-1"></span>
			<Button variant="primary" size="sm" onclick={() => (filtersOpen = false)}>Show {plural(total, 'brew')}</Button>
		</div>
	</BaseModal>
{/if}

<ConfirmModal
	isOpen={!!confirm}
	title={confirmCopy.title}
	message={confirmCopy.message}
	variant="danger"
	confirmLabel={confirmCopy.label}
	busy={stopping}
	on:confirm={() => stop(confirm?.kind === 'all' ? 'all' : stoppable.map((run) => run.id))}
	on:cancel={() => (confirm = null)}
/>

<style>
	.search {
		display: flex;
		height: 2rem;
		min-width: 0;
		max-width: 340px;
		flex: 1 1 200px;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.625rem;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
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

	.clear {
		display: grid;
		width: 20px;
		height: 20px;
		place-items: center;
		border-radius: 4px;
		color: rgb(var(--fg-subtle));
	}

	.clear:hover {
		color: rgb(var(--fg));
		background: rgb(var(--surface-3));
	}

	.seg {
		display: inline-flex;
		gap: 2px;
		padding: 2px;
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		background: rgb(var(--surface-2));
	}

	.seg button {
		display: inline-flex;
		height: 26px;
		align-items: center;
		padding: 0 10px;
		border-radius: 4px;
		color: rgb(var(--fg-muted));
		font-size: 12px;
		white-space: nowrap;
	}

	.seg button:hover {
		color: rgb(var(--fg));
	}

	.seg button[aria-checked='true'] {
		background: rgb(var(--surface-3));
		box-shadow: inset 0 0 0 1px rgb(var(--line-strong));
		color: rgb(var(--fg));
		font-weight: 600;
	}

	.pick,
	.filters-btn {
		display: inline-flex;
		height: 2rem;
		align-items: center;
		gap: 0.5rem;
		padding: 0 0.625rem;
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		background: rgb(var(--surface-2));
		color: rgb(var(--fg-muted));
		font-size: 12px;
	}

	.pick {
		width: 100%;
		justify-content: space-between;
	}

	.pick:hover,
	.filters-btn:hover {
		border-color: rgb(var(--line-hover));
		color: rgb(var(--fg));
	}

	.pick.set,
	.filters-btn.set {
		border-color: rgb(var(--signal) / 0.5);
		color: rgb(var(--signal));
	}

	.date input {
		height: 2rem;
		padding: 0 0.5rem;
		border: 1px solid rgb(var(--field-border));
		border-radius: 4px;
		background: rgb(var(--field-bg));
		color: rgb(var(--fg));
		font-size: 12px;
		font-family: 'IBM Plex Mono', monospace;
		color-scheme: inherit;
	}

	.label {
		margin-bottom: 0.5rem;
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}

	.opt {
		height: 36px;
		padding: 0 12px;
		border: 1px solid rgb(var(--line));
		border-radius: 4px;
		background: rgb(var(--surface-2));
		color: rgb(var(--fg-muted));
		font-size: 14px;
	}

	.opt[aria-pressed='true'] {
		border-color: rgb(var(--signal) / 0.5);
		background: rgb(var(--signal) / 0.1);
		color: rgb(var(--signal));
	}

	.mrow {
		position: relative;
		display: grid;
		min-height: 56px;
		grid-template-columns: 32px minmax(0, 1.6fr) 176px 108px 80px 60px 64px 40px;
		align-items: center;
		gap: 12px;
		padding: 8px 16px;
		border-bottom: 1px solid rgb(var(--line));
	}

	.mrow.head {
		position: sticky;
		top: 0;
		z-index: 2;
		min-height: 34px;
		padding-top: 6px;
		padding-bottom: 6px;
		background: rgb(var(--surface-1));
	}

	.mrow:not(.head):hover {
		background: rgb(var(--surface-2) / 0.6);
	}

	.mrow.sel {
		background: rgb(var(--signal) / 0.08);
	}

	.mrow.stopping .open {
		color: rgb(var(--fg-muted));
	}

	.head-cell {
		font-family: 'IBM Plex Mono', monospace;
		font-size: 12px;
		letter-spacing: 0.06em;
		text-transform: uppercase;
		color: rgb(var(--fg-subtle));
	}

	.num {
		text-align: right;
	}

	.chk {
		position: relative;
		z-index: 1;
		display: grid;
		width: 32px;
		height: 32px;
		place-items: center;
		margin-left: -8px;
		cursor: pointer;
	}

	.open {
		max-width: 100%;
		text-align: left;
	}

	.open::after {
		content: '';
		position: absolute;
		inset: 0;
	}

	.open:focus-visible::after {
		outline: 2px solid rgb(var(--signal));
		outline-offset: -2px;
	}

	.act,
	.col-started :global(*) {
		position: relative;
		z-index: 1;
	}

	.rel {
		cursor: default;
		border-bottom: 1px dotted rgb(var(--line-strong));
	}

	.chip {
		display: inline-flex;
		height: 20px;
		max-width: 160px;
		flex-shrink: 1;
		align-items: center;
		padding: 0 6px;
		border-radius: 4px;
		background: rgb(var(--surface-3));
		color: rgb(var(--fg-muted));
		font-size: 12px;
	}

	.dim {
		opacity: 0.6;
		transition: opacity 120ms;
	}

	.sub,
	.show-md {
		display: none;
	}

	@media (max-width: 1023px) {
		.mrow {
			grid-template-columns: 32px minmax(0, 1fr) 160px 96px 76px 40px;
		}

		.col-steps,
		.col-outputs {
			display: none;
		}

		.show-md {
			display: inline;
		}

		.hide-md {
			display: none;
		}
	}

	@media (max-width: 767px) {
		.mrow {
			grid-template-columns: 32px minmax(0, 1fr) 40px;
			align-items: start;
			padding: 10px 12px;
		}

		.mrow.head {
			display: none;
		}

		.col-status,
		.col-started,
		.col-duration {
			display: none;
		}

		.sub {
			display: flex;
		}

		.act {
			align-self: center;
		}

		.chk {
			margin-top: 0;
			align-self: center;
		}

		.tools {
			padding-bottom: 0.75rem;
		}
	}
</style>
