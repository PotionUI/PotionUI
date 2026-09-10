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
	import { tick } from 'svelte';
	import { browser } from '$app/environment';
	import portal from '$lib/actions/portal';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';

	// Matches the panel's former `mt-2`/`mb-2` gap from the trigger.
	const PANEL_GAP = 8;

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
	let panelEl: HTMLDivElement | undefined = $state();
	let searchEl: HTMLInputElement | undefined = $state();
	let activeId = $state<string | null>(null);
	let wasOpen = false;
	// The panel is portaled to <body>, so it needs its own fixed top/left/bottom
	// computed from the trigger's rect — null until the first measurement lands,
	// which keeps it hidden instead of flashing at its default (0, 0).
	let panelPosition = $state<FlippedMenuPosition | null>(null);

	function toggle() {
		open = !open;
	}

	function close() {
		open = false;
	}

	async function reposition() {
		if (!open) return;
		// Waits for the panel's own DOM node (the `{#if open}` block below) to
		// commit before measuring it — it isn't guaranteed to exist yet the
		// instant this runs, e.g. on the very first open.
		await tick();
		if (!open || !rootEl || !panelEl) return;
		panelPosition = computeFlippedMenuPosition(rootEl, {
			width: panelEl.offsetWidth,
			heightEstimate: panelEl.offsetHeight,
			gap: PANEL_GAP,
			align,
			preferred: placement === 'up' ? 'up' : 'down'
		});
	}

	function panelStyle(pos: FlippedMenuPosition | null): string {
		if (!pos) return 'visibility: hidden; top: 0; left: 0;';
		const vertical = pos.top !== undefined ? `top: ${pos.top}px;` : `bottom: ${pos.bottom}px;`;
		return `${vertical} left: ${pos.left}px;`;
	}

	$effect(() => {
		if (open && !wasOpen) {
			onOpen?.();
			queueMicrotask(() => searchEl?.focus());
			reposition();
		}
		if (!open) {
			panelPosition = null;
		}
		wasOpen = open;
	});

	// Keeps the panel anchored to the trigger while scrolling an ancestor
	// (e.g. a modal body) or resizing the window; torn down on close/destroy.
	$effect(() => {
		if (!browser || !open) return;
		window.addEventListener('resize', reposition);
		window.addEventListener('scroll', reposition, true);
		return () => {
			window.removeEventListener('resize', reposition);
			window.removeEventListener('scroll', reposition, true);
		};
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
		if (!open) return;
		const target = event.target as Node;
		if (rootEl?.contains(target) || panelEl?.contains(target)) return;
		close();
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
</div>

<!-- Portaled to <body> with `position: fixed`: an ancestor with `overflow: auto`
     (e.g. BaseModal's body) still counts an absolutely positioned descendant
     toward its scrollable area, which grew the modal instead of just opening
     a panel over it. -->
{#if open}
	<div
		bind:this={panelEl}
		use:portal
		style={panelStyle(panelPosition)}
		class="fixed z-[99999] bg-surface-1 border border-line-strong rounded-lg shadow-floating overflow-hidden flex flex-col {panelClass}"
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
