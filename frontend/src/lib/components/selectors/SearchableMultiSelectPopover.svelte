<script lang="ts" module>
	export interface PopoverPanelProps {
		activeId: string | null;
		optionId: (id: string) => string;
		listboxId: string;
	}

	export interface PopoverTriggerProps {
		open: boolean;
		toggle: () => void;
	}
</script>

<script lang="ts">
	import type { Snippet } from 'svelte';

	let {
		open = $bindable(false),
		placement = 'down',
		align = 'left',
		panelClass = '',
		showSearch = true,
		searchValue = $bindable(''),
		searchPlaceholder = 'Search...',
		optionIds = [],
		onSelect,
		onOpen,
		trigger,
		panel
	}: {
		open?: boolean;
		placement?: 'down' | 'up';
		align?: 'left' | 'right';
		panelClass?: string;
		showSearch?: boolean;
		searchValue?: string;
		searchPlaceholder?: string;
		/** Ids of the currently visible/navigable options, in display order. */
		optionIds?: string[];
		/** Fires when Enter/Space activates the highlighted option. */
		onSelect?: (id: string) => void;
		/** Fires once per closed-to-open transition — the hook for lazy hydration. */
		onOpen?: () => void;
		trigger: Snippet<[PopoverTriggerProps]>;
		panel: Snippet<[PopoverPanelProps]>;
	} = $props();

	const uid = $props.id();
	const listboxId = `${uid}-listbox`;
	const optionId = (id: string) => `${uid}-option-${id}`;

	let rootEl: HTMLDivElement | undefined = $state();
	let searchEl: HTMLInputElement | undefined = $state();
	let activeId = $state<string | null>(null);
	let wasOpen = false;

	function toggle() {
		open = !open;
	}

	function close() {
		open = false;
	}

	$effect(() => {
		if (open && !wasOpen) {
			onOpen?.();
			queueMicrotask(() => searchEl?.focus());
		}
		wasOpen = open;
	});

	// Keeps the highlighted option valid as the option list changes under filtering.
	$effect(() => {
		if (!open) {
			activeId = null;
		} else if (activeId === null || !optionIds.includes(activeId)) {
			activeId = optionIds[0] ?? null;
		}
	});

	function handleWindowClick(event: MouseEvent) {
		if (open && rootEl && !rootEl.contains(event.target as Node)) close();
	}

	function moveActive(delta: number) {
		if (optionIds.length === 0) return;
		const currentIndex = activeId ? optionIds.indexOf(activeId) : -1;
		const nextIndex = (currentIndex + delta + optionIds.length) % optionIds.length;
		activeId = optionIds[nextIndex];
	}

	function handleKeydown(event: KeyboardEvent) {
		switch (event.key) {
			case 'Escape':
				event.preventDefault();
				close();
				break;
			case 'ArrowDown':
				event.preventDefault();
				moveActive(1);
				break;
			case 'ArrowUp':
				event.preventDefault();
				moveActive(-1);
				break;
			case 'Home':
				event.preventDefault();
				if (optionIds.length) activeId = optionIds[0];
				break;
			case 'End':
				event.preventDefault();
				if (optionIds.length) activeId = optionIds[optionIds.length - 1];
				break;
			case 'Enter':
			case ' ':
				if (activeId) {
					event.preventDefault();
					onSelect?.(activeId);
				}
				break;
		}
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div class="relative inline-flex flex-col" bind:this={rootEl}>
	{@render trigger({ open, toggle })}

	{#if open}
		<div
			class="absolute z-50 {placement === 'up' ? 'bottom-full mb-2' : 'top-full mt-2'} {align === 'right' ? 'right-0' : 'left-0'} bg-surface-1 border border-line-strong rounded-lg shadow-floating overflow-hidden flex flex-col {panelClass}"
		>
			{#if showSearch}
				<input
					bind:this={searchEl}
					bind:value={searchValue}
					onkeydown={handleKeydown}
					type="text"
					placeholder={searchPlaceholder}
					class="w-full px-3 py-2 text-xs bg-surface-2 border-0 border-b border-line text-fg placeholder:text-fg-subtle outline-none"
					role="combobox"
					aria-expanded={open}
					aria-controls={listboxId}
					aria-activedescendant={activeId ? optionId(activeId) : undefined}
					aria-autocomplete="list"
				/>
			{/if}
			{@render panel({ activeId, optionId, listboxId })}
		</div>
	{/if}
</div>
