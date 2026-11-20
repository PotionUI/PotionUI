<script module lang="ts">
	export interface HeaderOption {
		value: string;
		label: string;
		description?: string;
		count?: number;
		icon?: string;
		group?: string;
		groupIcon?: string;
		pinned?: boolean;
		thumb?: { id: string; name: string; cover?: string | null; category?: string | null };
	}
</script>

<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import DropdownShell from '$lib/components/drawer/DropdownShell.svelte';
	import DrawerHeader from '$lib/components/drawer/DrawerHeader.svelte';
	import DrawerSearch from '$lib/components/drawer/DrawerSearch.svelte';
	import PresetThumbnail from '$lib/components/preset/PresetThumbnail.svelte';
	import { isMobile } from '$lib/stores/viewport';

	let {
		options,
		value,
		onchange,
		label,
		ariaLabel,
		active = false,
		block = false,
		joined = false,
		compact = false,
		searchable,
		placeholder = 'Select',
		searchPlaceholder
	}: {
		options: HeaderOption[];
		value: string;
		onchange: (value: string) => void;
		label?: string;
		ariaLabel?: string;
		active?: boolean;
		block?: boolean;
		joined?: boolean;
		compact?: boolean;
		searchable?: boolean;
		placeholder?: string;
		searchPlaceholder?: string;
	} = $props();

	const uid = $props.id();

	let open = $state(false);
	let anchor = $state<HTMLElement>();
	let query = $state('');
	let searchEl = $state<HTMLInputElement>();
	let triggerWidth = $state(0);

	const selected = $derived(options.find((option) => option.value === value));
	const hasSearch = $derived(searchable ?? options.filter((option) => !option.pinned).length > 6);
	const panelLabel = $derived(ariaLabel ?? label ?? 'Choose');
	const width = $derived(compact ? Math.max(triggerWidth, 220) : Math.min(420, Math.max(triggerWidth, 360)));

	const needle = $derived(query.trim().toLowerCase());
	const visible = $derived(
		needle
			? options.filter(
					(option) =>
						!option.pinned &&
						[option.label, option.description ?? '', option.group ?? ''].join(' ').toLowerCase().includes(needle)
				)
			: options
	);
	const pinned = $derived(visible.filter((option) => option.pinned || (option.value === '' && !needle)));
	const regular = $derived(visible.filter((option) => !pinned.includes(option)));
	const groups = $derived.by(() => {
		const list: Array<{ key: string; label: string; icon?: string; items: HeaderOption[] }> = [];
		for (const option of regular) {
			const key = option.group ?? '';
			let group = list.find((entry) => entry.key === key);
			if (!group) {
				group = { key, label: key, icon: option.groupIcon, items: [] };
				list.push(group);
			}
			group.items.push(option);
		}
		return list;
	});

	function toggle() {
		if (open) close();
		else {
			triggerWidth = anchor?.getBoundingClientRect().width ?? 0;
			open = true;
		}
	}

	function close() {
		open = false;
		query = '';
	}

	function choose(next: string) {
		close();
		onchange(next);
	}

	function handleTriggerKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' && !open) {
			event.preventDefault();
			toggle();
		}
	}

	function handlePanelKeydown(event: KeyboardEvent) {
		const target = event.target as HTMLElement;
		if (event.key === 'Enter' && target === searchEl) {
			event.preventDefault();
			if (needle && regular[0]) choose(regular[0].value);
			return;
		}
		if (hasSearch && target !== searchEl && event.key.length === 1 && !event.ctrlKey && !event.metaKey && !event.altKey && event.key !== '/') {
			searchEl?.focus();
		}
	}

	function parts(text: string): [string, string, string] | null {
		if (!needle) return null;
		const index = text.toLowerCase().indexOf(needle);
		if (index < 0) return null;
		return [text.slice(0, index), text.slice(index, index + needle.length), text.slice(index + needle.length)];
	}

	const surface = $derived(
		active
			? 'border-signal/25 bg-signal/10 text-signal'
			: block
				? 'border-line bg-surface-3/50 text-fg hover:bg-surface-3'
				: 'border-line-strong bg-surface-2/50 text-fg hover:bg-surface-3/60'
	);
</script>

<div class={block ? 'w-full' : 'flex-none'} bind:this={anchor}>
	<button
		type="button"
		class="flex h-8 items-center gap-1.5 whitespace-nowrap border pl-2.5 pr-2 text-sm transition-colors focus-visible:ring-2 focus-visible:ring-signal {joined
			? 'rounded-l rounded-r-none'
			: 'rounded'} {block ? 'w-full' : 'inline-flex'} {surface}"
		aria-haspopup="listbox"
		aria-expanded={open}
		aria-label={ariaLabel ?? label}
		onclick={toggle}
		onkeydown={handleTriggerKeydown}
	>
		{#if label}<span class={active ? '' : 'text-fg-subtle'}>{label}</span>{/if}
		<span class="min-w-0 flex-1 truncate text-left {block ? '' : 'max-w-[10rem]'}">{selected?.label ?? placeholder}</span>
		<Icon name="chevron-down" className="h-3 w-3 flex-none text-fg-subtle transition-transform {open ? 'rotate-180' : ''}" />
	</button>
</div>

{#snippet row(option: HeaderOption)}
	{@const current = option.value === value}
	{@const labelParts = parts(option.label)}
	<div class="picker-item mx-1.5 w-[calc(100%-0.75rem)] rounded">
		<button
			type="button"
			role="option"
			aria-selected={current}
			data-nav
			data-autofocus={current && !hasSearch ? '' : undefined}
			class="picker-row flex w-full items-center gap-3 rounded px-2 text-left {compact ? 'min-h-9 py-1.5' : option.description || option.thumb ? 'min-h-[52px] py-2' : 'min-h-10 py-2'}"
			class:is-current={current}
			onclick={() => choose(option.value)}
		>
			{#if option.thumb && !compact}
				<PresetThumbnail
					presetId={option.thumb.id}
					presetName={option.thumb.name}
					cover={option.thumb.cover}
					category={option.thumb.category}
					size="w-10 h-10 rounded-lg"
					zoom={false}
				/>
			{:else if option.icon}
				<Icon name={option.icon} className="h-4 w-4 flex-shrink-0 {current ? 'text-signal' : 'text-fg-muted'}" />
			{/if}
			<span class="flex min-w-0 flex-1 flex-col gap-0.5">
				<span class="truncate text-base font-medium leading-[18px] text-fg">
					{#if labelParts}{labelParts[0]}<b class="font-semibold text-signal">{labelParts[1]}</b>{labelParts[2]}{:else}{option.label}{/if}
				</span>
				{#if option.description && !compact}
					<span class="truncate text-sm leading-4 text-fg-subtle">{option.description}</span>
				{/if}
			</span>
			{#if option.count !== undefined}
				<span class="flex-shrink-0 font-mono text-sm tabular-nums text-fg-subtle">{option.count}</span>
			{/if}
			{#if current}
				<Icon name="check" className="h-3.5 w-3.5 flex-shrink-0 text-signal" />
			{/if}
		</button>
	</div>
{/snippet}

{#if open}
	<DropdownShell label={panelLabel} {anchor} {width} fullscreen={$isMobile} onClose={close} bind:searchEl>
		{#if $isMobile}
			<DrawerHeader title={panelLabel} closeLabel="Close {panelLabel.toLowerCase()}" onClose={close} />
		{/if}
		<div class="flex min-h-0 flex-1 flex-col" role="presentation" onkeydown={handlePanelKeydown}>
			{#if hasSearch}
				<DrawerSearch bind:value={query} bind:el={searchEl} placeholder={searchPlaceholder ?? `Search ${panelLabel.toLowerCase()}`} label="Search {panelLabel.toLowerCase()}" />
			{/if}
			<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1.5" role="listbox" aria-label={panelLabel} id="{uid}-list">
				{#if visible.length === 0}
					<div class="flex flex-col items-start gap-1 px-4 py-4">
						<p class="m-0 text-base font-semibold text-fg">No matches</p>
						<p class="m-0 text-sm text-fg-muted">Nothing matches “{query.trim()}”.</p>
						<Button variant="secondary" size="sm" class="mt-2" onclick={() => (query = '')}>Clear search</Button>
					</div>
				{:else}
					{#each pinned as option (option.value)}
						{@render row(option)}
					{/each}
					{#each groups as group (group.key)}
						{#if group.label}
							<div
								class="sticky top-0 z-[1] flex items-center gap-1.5 bg-surface-1 px-3.5 pb-1 pt-2.5 text-sm leading-5 text-fg-subtle"
								role="presentation"
							>
								{#if group.icon}<Icon name={group.icon} className="h-3.5 w-3.5 flex-shrink-0" />{/if}
								<span>{group.label}</span>
								<span class="ml-auto font-mono tabular-nums">{group.items.length}</span>
							</div>
						{/if}
						{#each group.items as option (option.value)}
							{@render row(option)}
						{/each}
					{/each}
				{/if}
			</div>
		</div>
	</DropdownShell>
{/if}

<style>
	.picker-row:focus-visible {
		outline: none;
	}

	.picker-item:hover {
		background: rgb(var(--surface-2));
	}

	.picker-item:has(.picker-row:focus-visible) {
		background: rgb(var(--surface-3));
	}

	.picker-item:has(.picker-row.is-current) {
		background: rgb(var(--signal) / 0.1);
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}

	.picker-item:has(.picker-row.is-current):hover,
	.picker-item:has(.picker-row.is-current:focus-visible) {
		background: rgb(var(--signal) / 0.16);
	}
</style>
