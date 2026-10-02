<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Kbd } from '$lib/components/ui';
	import portal from '$lib/actions/portal';
	import overlayLayer from '$lib/actions/overlayLayer';
	import { computeFlippedMenuPosition, type FlippedMenuPosition } from '$lib/utils/menuPosition';
	import { sourceEntries, type MediaSource } from './mediaFieldSources';
	import type { MediaKind } from './mediaLoaderConfig';

	let {
		kind,
		offersDraw = false,
		align = 'left',
		onPick,
		trigger
	}: {
		kind: MediaKind | null;
		offersDraw?: boolean;
		align?: 'left' | 'right';
		onPick: (source: MediaSource) => void;
		trigger: Snippet<[{ toggle: () => void; open: boolean }]>;
	} = $props();

	const MENU_WIDTH = 224;

	let open = $state(false);
	let anchor: HTMLDivElement | undefined = $state();
	let menuEl: HTMLDivElement | undefined = $state();
	let position = $state<FlippedMenuPosition>({ left: 0, top: 0, maxHeight: 320 });

	let entries = $derived(sourceEntries({ kind, offersDraw }));
	let positionStyle = $derived(
		`left: ${position.left}px; ${position.top !== undefined ? `top: ${position.top}px;` : `bottom: ${position.bottom}px;`} max-height: ${position.maxHeight}px; width: ${MENU_WIDTH}px;`
	);

	function reposition() {
		if (!anchor) return;
		position = computeFlippedMenuPosition(anchor, {
			width: MENU_WIDTH,
			heightEstimate: menuEl?.getBoundingClientRect().height || 220,
			align,
			preferred: 'down'
		});
	}

	function toggle() {
		open = !open;
	}

	function close() {
		open = false;
	}

	function items(): HTMLButtonElement[] {
		return menuEl ? Array.from(menuEl.querySelectorAll<HTMLButtonElement>('[role="menuitem"]')) : [];
	}

	function focusItem(index: number) {
		const all = items();
		if (all.length > 0) all[(index + all.length) % all.length].focus();
	}

	$effect(() => {
		if (!open) return;
		reposition();
		requestAnimationFrame(() => {
			reposition();
			focusItem(0);
		});
	});

	function handleKeydown(event: KeyboardEvent) {
		if (!open) return;
		if (event.key === 'Escape') {
			close();
			(anchor?.querySelector('button') as HTMLButtonElement | null)?.focus();
			return;
		}
		if (event.key !== 'ArrowDown' && event.key !== 'ArrowUp') return;
		event.preventDefault();
		const at = items().indexOf(document.activeElement as HTMLButtonElement);
		focusItem(at + (event.key === 'ArrowDown' ? 1 : -1));
	}

	function handlePointerDown(event: PointerEvent) {
		if (!open) return;
		const target = event.target as Node;
		if (anchor?.contains(target) || menuEl?.contains(target)) return;
		close();
	}

	function pick(source: MediaSource) {
		close();
		onPick(source);
	}
</script>

<svelte:window onkeydown={handleKeydown} onpointerdown={handlePointerDown} />

<div class="inline-flex" bind:this={anchor}>
	{@render trigger({ toggle, open })}
</div>

{#if open}
	<div
		use:portal
		use:overlayLayer
		bind:this={menuEl}
		class="fixed z-overlay overflow-y-auto bg-surface-1 border border-line-strong rounded-xl shadow-overlay p-1 flex flex-col"
		style={positionStyle}
		role="menu"
		aria-label="Add media"
		data-source-menu
	>
		{#each entries as entry (entry.source)}
			<button
				type="button"
				role="menuitem"
				class="w-full px-3 py-1.5 text-sm text-left rounded transition-colors flex items-center gap-2 text-fg-muted hover:text-fg hover:bg-surface-2 focus-visible:bg-surface-2 focus-visible:text-fg"
				data-source={entry.source}
				onclick={() => pick(entry.source)}
			>
				<Icon name={entry.icon} className="w-4 h-4 shrink-0" />
				<span class="flex-1 min-w-0 truncate">{entry.label}</span>
				{#if entry.shortcut}
					<Kbd keys={entry.shortcut.split(' ')} size="md" />
				{/if}
			</button>
		{/each}
	</div>
{/if}
