<script lang="ts">
	import { onMount, untrack } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import ConfirmDialog from '$lib/components/modals/ConfirmDialog.svelte';
	import { Badge, Input, Pagination, SegmentedControl, Spinner } from '$lib/components/ui';
	import { api } from '$lib/services/api/index';
import type { KitCreateResponse } from '$lib/services/api/kits';
	import { KIT_KIND_ICONS } from '$lib/kits/kitCounts';
	import { isNameClash, kitErrorMessage, kitErrorSuggestion } from '$lib/kits/kitErrors';
	import { checkName, liveName, slugify, suggestFree } from '$lib/kits/kitNaming';
	import { formatKitPath } from '$lib/kits/kitPath';
	import { describeLimit, picksNames, takenNames, toNewItems, type KitPick } from '$lib/kits/kitPicks';
	import { KIT_ITEM_LIMIT, type Kit, type KitDetail, type KitLabelCount, type NewKitItem } from '$lib/kits/types';
	import { bumpKits, rememberKit } from '$lib/stores/kits';
	import { toasts } from '$lib/stores/toast';
	import KitMosaic from './KitMosaic.svelte';
	import KitPath from './KitPath.svelte';

	let {
		picks = [],
		initialName = '',
		onDone,
		onClose
	}: {
		picks?: KitPick[];
		initialName?: string;
		onDone: (kit: KitDetail) => void;
		onClose: () => void;
	} = $props();

	const PAGE_SIZE = 4;

	let mode = $state<'new' | 'existing'>('new');
	let name = $state(untrack(() => initialName));
	let label = $state('');
	let names = $state<string[]>(untrack(() => picksNames(picks)));
	let edited = $state<boolean[]>(untrack(() => picks.map(() => false)));
	let busy = $state(false);
	let error = $state<string | null>(null);
	let clash = $state(false);

	let existing = $state<Kit[]>([]);
	let existingTotal = $state(0);
	let existingPage = $state(1);
	let existingSearch = $state('');
	let existingLoading = $state(false);
	let labels = $state<KitLabelCount[]>([]);
	let target = $state<KitDetail | null>(null);

	let hasItems = $derived(picks.length > 0);
	let handle = $derived(slugify(name));
	let nameValid = $derived(name.trim().length > 0 && handle.length > 0);
	let takenInKit = $derived(mode === 'existing' && target ? takenNames(target.items) : []);
	let rowChecks = $derived(
		names.map((value, index) =>
			checkName(value, [...takenInKit, ...names.filter((_, other) => other !== index)])
		)
	);
	let namesValid = $derived(rowChecks.every((check) => check.ok));
	let limitNote = $derived(
		hasItems ? describeLimit(mode === 'existing' && target ? target.items.length : 0, picks.length, KIT_ITEM_LIMIT) : null
	);
	let canSubmit = $derived(
		!limitNote && namesValid && (mode === 'new' ? nameValid : target !== null)
	);
	let title = $derived(
		hasItems ? `Add ${picks.length} ${picks.length === 1 ? 'item' : 'items'} to a kit` : 'New kit'
	);

	async function loadExisting() {
		existingLoading = true;
		try {
			const response = await api.listKits({ search: existingSearch, page: existingPage, page_size: PAGE_SIZE });
			if (response.success && response.data) {
				existing = response.data.kits;
				existingTotal = response.data.total;
				labels = response.data.labels;
			}
		} catch {
			existing = [];
		} finally {
			existingLoading = false;
		}
	}

	async function choose(kit: Kit) {
		try {
			const response = await api.getKit(kit.id);
			if (response.success && response.data) {
				target = response.data;
				renumber();
			}
		} catch {
			error = 'Could not open that kit.';
		}
	}

	function renumber() {
		const fresh = picksNames(picks, takenInKit);
		names = names.map((value, index) => (edited[index] ? value : fresh[index]));
	}

	function setMode(next: string) {
		mode = next === 'existing' ? 'existing' : 'new';
		error = null;
		if (mode === 'existing' && existing.length === 0) void loadExisting();
		renumber();
	}

	function rename(index: number, raw: string) {
		names[index] = liveName(raw);
		edited[index] = true;
	}

	onMount(() => {
		void loadExisting();
	});

	let createdKit = $state<KitDetail | null>(null);
	let pending = $state<NewKitItem[]>([]);
	let suggestion = $state<string | null>(null);

	function finish(kit: KitDetail, message: string) {
		rememberKit(kit);
		bumpKits();
		toasts.success(message);
		onDone(kit);
		onClose();
	}

	async function notAdded(kit: KitDetail, remaining: NewKitItem[]): Promise<NewKitItem[]> {
		try {
			const fresh = await api.getKit(kit.id);
			if (fresh.success && fresh.data) {
				const have = new Set(fresh.data.items.map((item) => item.name));
				return remaining.filter((item) => !item.name || !have.has(item.name));
			}
		} catch {
			return remaining;
		}
		return remaining;
	}

	function leftOver(kit: KitDetail, remaining: NewKitItem[], message: string) {
		createdKit = kit;
		pending = remaining;
		rememberKit(kit);
		bumpKits();
		const names = remaining.map((item) => item.name ?? item.kind).join(', ');
		error = `${kit.name} was created. Not added: ${names}. ${message} Press the button again to add them.`;
	}

	async function submit(): Promise<boolean> {
		busy = true;
		error = null;
		clash = false;
		suggestion = null;
		try {
			if (createdKit) {
				const kit = createdKit;
				try {
					const response = await api.addKitItems(kit.id, pending);
					if (response.success && response.data) {
						finish(response.data, `Created ${response.data.name}`);
						return true;
					}
					leftOver(kit, await notAdded(kit, pending), kitErrorMessage(response, ''));
				} catch (caught) {
					leftOver(kit, await notAdded(kit, pending), kitErrorMessage(caught, ''));
				}
				return false;
			}
			const items = toNewItems(picks, names);
			const response: KitCreateResponse =
				mode === 'new'
					? await api.createKit({ name: name.trim(), label: label.trim() || null, items })
					: await api.addKitItems((target as KitDetail).id, items);
			if (mode === 'new' && 'partial' in response && response.partial) {
				leftOver(response.partial.created, response.partial.pending, response.partial.message);
				return false;
			}
			if (!response.success || !response.data) {
				clash = isNameClash(response);
				suggestion = kitErrorSuggestion(response);
				error = kitErrorMessage(response, 'Could not save the kit.');
				return false;
			}
			finish(
				response.data,
				mode === 'new'
					? `Created ${response.data.name}`
					: `Added ${picks.length} ${picks.length === 1 ? 'item' : 'items'} to ${response.data.name}`
			);
			return true;
		} catch (caught) {
			clash = isNameClash(caught);
			suggestion = kitErrorSuggestion(caught);
			error = kitErrorMessage(caught, 'Could not save the kit.');
			return false;
		} finally {
			busy = false;
		}
	}

	let totalPages = $derived(Math.max(1, Math.ceil(existingTotal / PAGE_SIZE)));
</script>

<ConfirmDialog
	isOpen={true}
	size="lg"
	{title}
	{busy}
	confirmLabel={mode === 'new' ? (hasItems ? 'Create kit' : 'Create') : 'Add to kit'}
	confirmDisabled={!canSubmit}
	onConfirm={submit}
	onCancel={onClose}
>
	{#snippet leftActions()}
		{#if hasItems}<span class="whitespace-nowrap font-mono text-sm tabular-nums text-fg-subtle">{picks.length} {picks.length === 1 ? 'item' : 'items'}</span>{/if}
	{/snippet}
	<div class="space-y-5 p-4 md:p-5">
		{#if hasItems}
			<SegmentedControl
				variant="toggle"
				fullWidth
				ariaLabel="Where"
				selected={mode}
				onSelect={setMode}
				items={[
					{ id: 'new', label: 'New kit' },
					{ id: 'existing', label: 'Existing kit' }
				]}
			/>
		{/if}

		{#if mode === 'new'}
			<div class="grid gap-4 sm:grid-cols-[1.4fr_1fr]">
				<div>
					<label class="label" for="compose-name">Name</label>
					<Input id="compose-name" bind:value={name} autocomplete="off" class="text-base" disabled={createdKit !== null} data-autofocus />
					<p class="mt-1.5 font-mono text-sm text-fg-subtle">
						{#if handle}<KitPath {handle} />{:else}{formatKitPath('…')}{/if}
					</p>
				</div>
				<div>
					<label class="label" for="compose-label">Label <span class="normal-case tracking-normal text-fg-subtle">· optional</span></label>
					<Input id="compose-label" bind:value={label} autocomplete="off" class="text-base" list="kit-label-options" />
					<datalist id="kit-label-options">
						{#each labels as entry (entry.label)}<option value={entry.label}></option>{/each}
					</datalist>
				</div>
			</div>
		{:else}
			<div>
				<p class="label">Kit</p>
				<div class="relative mb-2">
					<Icon name="search" className="pointer-events-none absolute left-2.5 top-1/2 h-3.5 w-3.5 -translate-y-1/2 text-fg-subtle" />
					<input
						type="text"
						class="input w-full py-1.5 pl-8 text-sm"
						placeholder="Search kits"
						aria-label="Search kits"
						bind:value={existingSearch}
						oninput={() => {
							existingPage = 1;
							void loadExisting();
						}}
					/>
				</div>
				<div class="overflow-hidden rounded border border-line" role="radiogroup" aria-label="Kit">
					{#if existingLoading && existing.length === 0}
						<div class="flex justify-center py-6"><Spinner /></div>
					{:else if existing.length === 0}
						<p class="px-3 py-4 text-center text-sm text-fg-muted">No kits match.</p>
					{/if}
					{#each existing as kit (kit.id)}
						<label
							class="flex cursor-pointer items-center gap-3 border-b border-line px-3 py-2 last:border-b-0 {target?.id === kit.id
								? 'bg-signal/10'
								: 'hover:bg-surface-2/50'}"
						>
							<input type="radio" name="kit-target" class="sr-only" checked={target?.id === kit.id} onchange={() => void choose(kit)} />
							<span
								class="flex h-4 w-4 flex-shrink-0 items-center justify-center rounded-full border {target?.id === kit.id ? 'border-signal' : 'border-line-strong'}"
							>
								{#if target?.id === kit.id}<span class="h-2 w-2 rounded-full bg-signal"></span>{/if}
							</span>
							<span class="h-9 w-12 flex-shrink-0 overflow-hidden rounded-sm"><KitMosaic items={kit.preview_items} class="h-9" /></span>
							<span class="min-w-0 flex-1">
								<span class="block truncate text-base text-fg">{kit.name}</span>
								<KitPath handle={kit.handle} class="block text-sm text-fg-subtle" />
							</span>
							{#if kit.label}<Badge size="sm">{kit.label}</Badge>{/if}
							<span class="font-mono text-sm tabular-nums text-fg-subtle">{kit.item_count}</span>
						</label>
					{/each}
				</div>
				{#if totalPages > 1}
					<div class="mt-2 flex justify-end">
						<Pagination
							size="sm"
							currentPage={existingPage}
							{totalPages}
							onPageChange={(next) => {
								existingPage = next;
								void loadExisting();
							}}
						/>
					</div>
				{/if}
			</div>
		{/if}

		{#if hasItems}
			<div>
				<div class="mb-1.5 flex items-baseline justify-between gap-3">
					<p class="label !mb-0">Items</p>
					<span class="text-sm text-fg-subtle">Named for you. Rename any of them now or later.</span>
				</div>
				<ul class="divide-y divide-line overflow-hidden rounded border border-line">
					{#each picks as pick, index (pick.key)}
						<li class="flex items-center gap-3 bg-surface-1 px-3 py-2" data-compose-row>
							<span class="h-10 w-10 flex-shrink-0 overflow-hidden rounded">
								{#if pick.thumbnail && (pick.kind === 'image' || pick.kind === 'video')}
									<img src={pick.thumbnail} alt="" class="h-full w-full object-cover" loading="lazy" />
								{:else}
									<span class="flex h-full items-center justify-center bg-surface-2 text-fg-muted">
										<Icon name={KIT_KIND_ICONS[pick.kind]} className="h-4 w-4" />
									</span>
								{/if}
							</span>
							<span class="min-w-0 flex-1">
								<span class="block truncate text-sm text-fg-subtle">{pick.label}</span>
								{#if !rowChecks[index]?.ok && rowChecks[index]?.message}
									<span class="block text-sm text-danger" role="alert">
										{rowChecks[index].message}
										{#if rowChecks[index].suggestion}
											<button type="button" class="font-mono underline" onclick={() => rename(index, suggestFree(names[index], [...takenInKit, ...names]))}>
												{suggestFree(names[index], [...takenInKit, ...names])}
											</button>
										{/if}
									</span>
								{/if}
							</span>
							<span class="flex items-center gap-1.5 text-fg-subtle"><Icon name={KIT_KIND_ICONS[pick.kind]} className="h-3.5 w-3.5" /></span>
							<input
								class="h-8 w-40 rounded border bg-field-bg px-2 font-mono text-sm text-fg focus:outline-none {rowChecks[index]?.ok ? 'border-line-strong focus:border-signal' : 'border-danger'}"
								value={names[index]}
								aria-label="Name for {pick.label}"
								aria-invalid={!rowChecks[index]?.ok}
								oninput={(event) => {
									const input = event.currentTarget as HTMLInputElement;
									rename(index, input.value);
									input.value = names[index];
								}}
							/>
						</li>
					{/each}
				</ul>
			</div>
		{/if}

		{#if limitNote}<p class="text-sm text-danger" role="alert">{limitNote}</p>{/if}
		{#if error}
			<p class="text-sm text-danger" role="alert">
				{clash ? 'That path name is already used.' : error}
				{#if clash && (suggestion || handle)}
					<button type="button" class="font-mono underline" onclick={() => { name = suggestion ?? suggestFree(handle, [handle]); clash = false; suggestion = null; error = null; }}>{suggestion ?? suggestFree(handle, [handle])}</button>
				{/if}
			</p>
		{/if}
	</div>
</ConfirmDialog>
