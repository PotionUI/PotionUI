<script module lang="ts">
	export interface PickerOption {
		value: string;
		label: string;
		icon?: string;
		description?: string;
		marker?: string;
		markerLabel?: string;
		issue?: string;
		notice?: string;
		action?: { label: string; onclick: () => void };
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import Button from '$lib/components/ui/Button.svelte';
	import DropdownShell from '$lib/components/drawer/DropdownShell.svelte';
	import DrawerHeader from '$lib/components/drawer/DrawerHeader.svelte';
	import { isMobile } from '$lib/stores/viewport';

	let {
		value = '',
		options = [],
		label,
		placeholder = 'Select',
		width = 400,
		triggerDescription = false,
		onchange,
		trigger = undefined
	}: {
		value?: string | null;
		options?: PickerOption[];
		label: string;
		placeholder?: string;
		width?: number;
		triggerDescription?: boolean;
		onchange: (value: string) => void;
		trigger?: Snippet<[{ open: boolean; selected: PickerOption | undefined; toggle: () => void; keydown: (event: KeyboardEvent) => void }]>;
	} = $props();

	const uid = $props.id();

	let open = $state(false);
	let anchor = $state<HTMLElement>();
	let selected = $derived(options.find((option) => option.value === value));

	function choose(next: string) {
		open = false;
		onchange(next);
	}

	function handleTriggerKeydown(event: KeyboardEvent) {
		if (event.key === 'ArrowDown' && !open) {
			event.preventDefault();
			open = true;
		}
	}
</script>

{#if trigger}
	<div class="flex min-w-0" bind:this={anchor}>
		{@render trigger({ open, selected, toggle: () => (open = !open), keydown: handleTriggerKeydown })}
	</div>
{:else}
<button
	type="button"
	class="flex h-full w-full min-w-0 items-center gap-2 rounded border border-line-strong bg-surface-1 px-3 py-2 text-left transition-colors hover:border-line-hover hover:bg-surface-2 focus-visible:ring-1 focus-visible:ring-signal/50"
	bind:this={anchor}
	aria-haspopup="listbox"
	aria-expanded={open}
	onclick={() => (open = !open)}
	onkeydown={handleTriggerKeydown}
>
	{#if selected?.icon}<Icon name={selected.icon} className="w-3.5 h-3.5 flex-shrink-0 text-fg-muted" />{/if}
	<span class="flex min-w-0 flex-1 flex-col">
		{#if selected}
			<span class="truncate text-sm font-semibold text-fg">{selected.label}</span>
			{#if triggerDescription && selected.description}
				<span class="truncate text-sm text-fg-subtle">{selected.description}</span>
			{/if}
		{:else}
			<span class="truncate text-sm text-fg-subtle">{placeholder}</span>
		{/if}
	</span>
	{#if selected?.marker}
		<span class="-ml-1 flex-shrink-0 font-mono text-sm text-fg-subtle" aria-hidden="true">{selected.marker}</span>
	{/if}
	<Icon name="chevron-down" className="w-4 h-4 text-fg-subtle flex-shrink-0 transition-transform {open ? 'rotate-180' : ''}" />
</button>
{/if}

{#if open}
	<DropdownShell {label} {anchor} {width} fullscreen={$isMobile} onClose={() => (open = false)}>
		{#if $isMobile}
			<DrawerHeader title={label} closeLabel="Close {label.toLowerCase()}" onClose={() => (open = false)} />
		{/if}
		<div class="min-h-0 flex-1 overflow-y-auto overscroll-contain py-1.5" role="listbox" aria-label={label} id="{uid}-list">
			{#each options as option (option.value)}
				{@const current = option.value === value}
				<div class="picker-item mx-1.5 w-[calc(100%-0.75rem)] rounded" class:is-current={current}>
					<button
						type="button"
						role="option"
						aria-selected={current}
						data-nav
						data-autofocus={current ? '' : undefined}
						class="picker-row flex w-full items-center gap-3 rounded px-2 pt-2 text-left {option.action ? 'pb-1' : 'pb-2'}"
						onclick={() => choose(option.value)}
					>
						{#if option.icon}<Icon name={option.icon} className="w-4 h-4 flex-shrink-0 {current ? 'text-signal' : 'text-fg-muted'}" />{/if}
						<span class="flex min-w-0 flex-1 flex-col gap-0.5">
							<span class="flex min-w-0 items-center gap-1.5">
								<span class="truncate text-base font-medium leading-[18px] text-fg">{option.label}</span>
								{#if option.marker}
									<span class="flex-shrink-0 font-mono text-sm text-fg-subtle" aria-hidden="true">{option.marker}</span>
								{/if}
								{#if option.markerLabel}
									<span class="sr-only">{option.markerLabel}</span>
								{/if}
							</span>
							{#if option.description}
								<span class="line-clamp-2 text-sm leading-4 text-fg-subtle">{option.description}</span>
							{/if}
							{#if option.issue}
								<span class="flex min-w-0 items-center gap-1.5 text-sm leading-4 text-warning" data-option-issue>
									<span class="h-1.5 w-1.5 flex-shrink-0 rounded-full bg-current" aria-hidden="true"></span>
									<span class="truncate">{option.issue}</span>
								</span>
							{/if}
							{#if option.notice}
								<span class="flex min-w-0 items-center gap-1.5 text-sm leading-4 text-fg-subtle" data-option-notice>
									<Icon name="lock" className="h-3.5 w-3.5 flex-shrink-0" />
									<span>{option.notice}</span>
								</span>
							{/if}
						</span>
						{#if current}
							<Icon name="check" className="h-3.5 w-3.5 flex-shrink-0 text-signal" />
						{/if}
					</button>
					{#if option.action}
						{@const action = option.action}
						<div class={option.icon ? 'pb-2 pl-9 pr-2' : 'pb-2 pl-2 pr-2'}>
							<Button
								size="xs"
								icon="download"
								class="border border-line-strong font-mono tabular-nums"
								attrs={{ 'data-nav': '', 'data-option-action': '' }}
								onclick={() => {
									open = false;
									action.onclick();
								}}
							>
								{action.label}
							</Button>
						</div>
					{/if}
				</div>
			{/each}
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

	.picker-item.is-current {
		background: rgb(var(--signal) / 0.1);
		box-shadow: inset 2px 0 0 rgb(var(--signal));
	}

	.picker-item.is-current:hover,
	.picker-item.is-current:has(.picker-row:focus-visible) {
		background: rgb(var(--signal) / 0.16);
	}
</style>
