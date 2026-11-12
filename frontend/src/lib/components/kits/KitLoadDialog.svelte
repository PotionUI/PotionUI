<script lang="ts">
	import { onMount } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { Badge, Pagination, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
	import { acceptedSentence, mediaCountOf, usageOf, type AcceptedKinds } from '$lib/kits/kitCounts';
	import type { Kit, KitDetail, KitListPage } from '$lib/kits/types';
	import { kitTotal, rememberKit } from '$lib/stores/kits';
	import KitItemChip from './KitItemChip.svelte';
	import KitKindCounts from './KitKindCounts.svelte';
	import KitLabelFilter from './KitLabelFilter.svelte';
	import KitMosaic from './KitMosaic.svelte';
	import KitPath from './KitPath.svelte';

	let {
		loadedIds = [],
		accepted = null,
		modelName = null,
		subtitle = 'Reference media',
		mediaRoom = null,
		onLoad,
		onClose
	}: {
		loadedIds?: string[];
		accepted?: AcceptedKinds;
		modelName?: string | null;
		subtitle?: string;
		mediaRoom?: number | null;
		onLoad: (kits: KitDetail[]) => void;
		onClose: () => void;
	} = $props();

	const PAGE_SIZE = 6;

	let data = $state<KitListPage | null>(null);
	let loading = $state(true);
	let failed = $state(false);
	let search = $state('');
	let label = $state<string | null>(null);
	let page = $state(1);
	let chosen = $state<string[]>([]);
	let details = $state<Record<string, KitDetail>>({});
	let focusId = $state<string | null>(null);
	let busy = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;
	let requestId = 0;

	let justLoaded = $state<string[]>([]);
	let loadedSet = $derived(new Set([...loadedIds, ...justLoaded]));
	let chosenSet = $derived(new Set(chosen));
	let pages = $derived(data ? Math.max(1, Math.ceil(data.total / PAGE_SIZE)) : 1);
	let from = $derived(data && data.kits.length > 0 ? (page - 1) * PAGE_SIZE + 1 : 0);
	let to = $derived(data ? (page - 1) * PAGE_SIZE + data.kits.length : 0);
	let everything = $derived($kitTotal ?? data?.total ?? 0);
	let focus = $derived(focusId ? (details[focusId] ?? null) : null);
	let focusUsage = $derived(focus ? usageOf(focus.items, accepted) : null);
	let selectedMedia = $derived(
		chosen.reduce((sum, id) => sum + (details[id] ? mediaCountOf(details[id].items, accepted) : 0), 0)
	);
	let over = $derived(mediaRoom !== null && selectedMedia > mediaRoom);
	let confirmLabel = $derived(
		chosen.length === 0
			? 'Load'
			: chosen.length === 1
				? `Load ${details[chosen[0]]?.name ?? 'kit'}`
				: `Load ${chosen.length} kits`
	);

	async function load() {
		const mine = ++requestId;
		loading = true;
		failed = false;
		try {
			const response = await api.listKits({ search, label, page, page_size: PAGE_SIZE });
			if (mine !== requestId) return;
			if (response.success && response.data) data = response.data;
			else failed = true;
		} catch {
			if (mine === requestId) failed = true;
		} finally {
			if (mine === requestId) loading = false;
		}
	}

	let problems = $state<Record<string, string>>({});

	function why(error: unknown): string {
		const status = (error as { response?: { status?: number } } | null)?.response?.status;
		return status === 404 ? 'no longer exists' : 'could not be reached';
	}

	async function fetchDetail(id: string): Promise<KitDetail | null> {
		if (details[id]) return details[id];
		try {
			const response = await api.getKit(id);
			if (response.success && response.data) {
				details[id] = response.data;
				delete problems[id];
				return response.data;
			}
			problems[id] = 'could not be loaded';
		} catch (error) {
			problems[id] = why(error);
		}
		return null;
	}

	async function toggle(kit: Kit) {
		if (loadedIds.includes(kit.id)) return;
		if (chosen.includes(kit.id)) {
			chosen = chosen.filter((id) => id !== kit.id);
			if (focusId === kit.id) focusId = chosen[chosen.length - 1] ?? null;
			return;
		}
		chosen = [...chosen, kit.id];
		focusId = kit.id;
		await fetchDetail(kit.id);
	}

	function typed() {
		clearTimeout(timer);
		timer = setTimeout(() => {
			page = 1;
			void load();
		}, 300);
	}

	function pickLabel(next: string | null) {
		label = next;
		page = 1;
		void load();
	}

	async function confirm(): Promise<boolean> {
		busy = true;
		const loaded: KitDetail[] = [];
		for (const id of chosen) {
			const kit = await fetchDetail(id);
			if (kit) {
				rememberKit(kit);
				loaded.push(kit);
			}
		}
		busy = false;
		if (loaded.length > 0) onLoad(loaded);
		if (loaded.length === chosen.length) {
			onClose();
			return true;
		}
		justLoaded = [...justLoaded, ...loaded.map((kit) => kit.id)];
		chosen = chosen.filter((id) => !loaded.some((kit) => kit.id === id));
		return false;
	}

	let failedNames = $derived(
		Object.keys(problems)
			.filter((id) => chosen.includes(id))
			.map((id) => `${nameOf(id)} ${problems[id]}`)
	);

	function nameOf(id: string): string {
		return data?.kits.find((kit) => kit.id === id)?.name ?? id;
	}

	onMount(() => {
		void load();
		return () => clearTimeout(timer);
	});
</script>

<ConfirmDialog
	isOpen={true}
	size="lg"
	title="Load a kit"
	{subtitle}
	{busy}
	{confirmLabel}
	confirmDisabled={chosen.length === 0 || over}
	onConfirm={confirm}
	onCancel={onClose}
>
	{#snippet leftActions()}
		<span class="whitespace-nowrap font-mono text-sm tabular-nums {over ? 'text-warning' : 'text-fg-subtle'}">
			{#if over}{selectedMedia} media items, room for {mediaRoom}{:else}{chosen.length} selected{/if}
		</span>
	{/snippet}
	<div class="flex flex-col">
		<div class="flex flex-wrap items-center gap-3 border-b border-line px-5 py-3">
			<div class="relative min-w-[10rem] flex-1">
				<Icon name="search" className="pointer-events-none absolute left-3 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
				<input
					type="text"
					class="input w-full py-2 pl-9 text-base"
					placeholder="Search kits"
					aria-label="Search kits"
					bind:value={search}
					oninput={typed}
					data-autofocus
				/>
			</div>
			{#if data && data.labels.length > 0}
				<div class="no-scrollbar max-w-full overflow-x-auto">
					<KitLabelFilter labels={data.labels} total={everything} value={label} onChange={pickLabel} />
				</div>
			{/if}
		</div>

		{#if failedNames.length > 0}
			<div class="border-b border-danger/25 bg-danger/10 px-5 py-2 text-sm text-danger" role="alert" data-kit-load-problems>
				{#if justLoaded.length > 0}Some kits were loaded. {/if}Could not load: {failedNames.join('; ')}.
			</div>
		{/if}

		<div class="min-h-[120px]" role="listbox" aria-multiselectable="true" aria-label="Kits" data-kit-load-list>
			{#if loading && !data}
				<div class="flex justify-center py-10"><Spinner /></div>
			{:else if failed && !data}
				<p class="px-5 py-8 text-center text-sm text-danger">Could not load kits.</p>
			{:else if data && data.kits.length === 0}
				<p class="px-5 py-8 text-center text-sm text-fg-muted">No kits match.</p>
			{:else if data}
				{#each data.kits as kit (kit.id)}
					<button
						type="button"
						role="option"
						aria-selected={chosenSet.has(kit.id)}
						disabled={loadedSet.has(kit.id)}
						class="flex w-full items-center gap-3 border-b border-line px-5 py-2.5 text-left last:border-b-0 {chosenSet.has(kit.id)
							? 'bg-signal/10'
							: 'hover:bg-surface-2/50'} {loadedSet.has(kit.id) ? 'opacity-60' : ''}"
						onclick={() => void toggle(kit)}
					>
						<span
							class="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border {chosenSet.has(kit.id)
								? 'border-signal bg-signal text-canvas'
								: 'border-line-strong'}"
						>
							{#if chosenSet.has(kit.id)}<Icon name="check" className="h-3 w-3" strokeWidth={3} />{/if}
						</span>
						<span class="h-12 w-16 flex-shrink-0 overflow-hidden rounded-sm"><KitMosaic items={kit.preview_items} class="h-12" /></span>
						<span class="min-w-0 flex-1">
							<span class="flex min-w-0 items-center gap-2">
								<span class="truncate text-base font-semibold text-fg">{kit.name}</span>
								{#if kit.label}<Badge size="sm">{kit.label}</Badge>{/if}
							</span>
							<KitPath handle={kit.handle} class="block text-sm text-fg-subtle" />
						</span>
						{#if loadedSet.has(kit.id)}
							<span class="text-sm text-fg-subtle">Loaded</span>
						{:else}
							<KitKindCounts counts={kit.counts} class="text-fg-muted" />
						{/if}
					</button>
				{/each}
			{/if}
		</div>

		{#if focus && focusUsage}
			<div class="space-y-2 border-t border-line px-5 py-3" data-kit-load-preview>
				<div class="flex items-center justify-between gap-3">
					<span class="font-mono text-sm uppercase tracking-[0.07em] text-fg-subtle">
						{focus.name}{modelName ? ` on ${modelName}` : ''}
					</span>
					<span class="font-mono text-sm tabular-nums text-fg-subtle">{focus.items.length} items</span>
				</div>
				<div class="flex flex-wrap gap-1.5">
					{#each focus.items as item (item.id)}
						<KitItemChip {item} skipped={focusUsage.skipped.some((skipped) => skipped.id === item.id)} />
					{/each}
				</div>
				<p class="text-sm text-fg-muted">{acceptedSentence(accepted)}</p>
			</div>
		{/if}

		<div class="flex flex-wrap items-center justify-between gap-3 border-t border-line px-5 py-3">
			<span class="font-mono text-sm tabular-nums text-fg-subtle">{from}–{to} of {data?.total ?? 0} kits</span>
			<Pagination
				size="sm"
				currentPage={page}
				totalPages={pages}
				onPageChange={(next) => {
					page = next;
					void load();
				}}
			/>
		</div>
	</div>
</ConfirmDialog>
