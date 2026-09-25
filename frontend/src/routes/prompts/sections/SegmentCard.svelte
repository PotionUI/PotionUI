<script lang="ts">
	import type { SavedSegment } from '$lib/types/segments';
	import { Badge, Button, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';

	let {
		segment,
		categoryName = null,
		selected = false,
		onToggleSelect,
		onOpen,
		onInsert,
		onDuplicate,
		onDelete
	}: {
		segment: SavedSegment;
		categoryName?: string | null;
		selected?: boolean;
		onToggleSelect: (segment: SavedSegment) => void;
		onOpen: (segment: SavedSegment) => void;
		onInsert: (segment: SavedSegment) => void;
		onDuplicate: (segment: SavedSegment) => void;
		onDelete: (segment: SavedSegment) => void;
	} = $props();

	let menuOpen = $state(false);
	let menuEl: HTMLDivElement | undefined = $state();

	const color = $derived(segment.effective_color || segment.color || '#3B82F6');
	const tags = $derived((segment.tags ?? []).slice(0, 2));
	const relativeLabel = $derived.by(() => {
		const when = segment.updated_at ?? segment.created_at;
		return when ? timeAgo(when) : null;
	});

	function closeMenu() {
		menuOpen = false;
	}

	function handleWindowClick(event: MouseEvent) {
		if (menuOpen && menuEl && !menuEl.contains(event.target as Node)) closeMenu();
	}

	function handleCardKeydown(event: KeyboardEvent) {
		if (event.key === 'Enter') {
			event.preventDefault();
			onOpen(segment);
		} else if (event.key === ' ') {
			event.preventDefault();
			onToggleSelect(segment);
		}
	}

	function stop(fn: () => void) {
		return (event: Event) => {
			event.stopPropagation();
			fn();
		};
	}
</script>

<svelte:window onclick={handleWindowClick} />

<div
	class="group relative flex min-w-0 gap-3 rounded-lg border p-3 shadow-raised transition-colors {selected
		? 'border-signal bg-signal/[0.06] shadow-[0_0_0_1px_rgb(var(--signal))]'
		: 'border-line-strong bg-surface-1 hover:border-line-hover'}"
	role="button"
	tabindex="0"
	data-segment-card
	aria-label={segment.name}
	onclick={() => onOpen(segment)}
	onkeydown={handleCardKeydown}
>
	<button
		type="button"
		class="mt-1 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-opacity {selected
			? 'border-accent bg-accent text-accent-contrast opacity-100'
			: 'border-line-hover bg-surface-2 text-transparent opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}"
		onclick={stop(() => onToggleSelect(segment))}
		aria-label={selected ? 'Deselect segment' : 'Select segment'}
		aria-pressed={selected}
	>
		{#if selected}<Icon name="check" className="h-3 w-3" strokeWidth={3} />{/if}
	</button>

	<div class="flex min-w-0 flex-1 flex-col gap-1.5">
		<div class="flex min-w-0 items-center gap-2">
			<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background: {color}"></span>
			<h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{segment.name}</h3>
			{#if !segment.enabled}
				<Badge size="sm">Disabled</Badge>
			{/if}
		</div>

		<p class="line-clamp-3 text-sm leading-relaxed {segment.content ? 'text-fg-muted' : 'italic text-fg-subtle'}">
			{segment.content || 'Empty starter content'}
		</p>

		<div class="mt-auto flex h-6 min-w-0 items-center gap-1.5" data-card-footer>
			{#if categoryName}
				<Badge size="sm">{categoryName}</Badge>
			{/if}
			{#each tags as tag (tag)}
				<Badge size="sm" class="font-mono">#{tag}</Badge>
			{/each}
			<div class="ml-auto flex h-6 flex-shrink-0 items-center">
				{#if relativeLabel}
					<span
						class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle {menuOpen
							? 'hidden'
							: 'group-hover:hidden group-focus-within:hidden'}">{relativeLabel}</span
					>
				{/if}
				<div
					class="items-center gap-1 {menuOpen ? 'flex' : 'hidden group-hover:flex group-focus-within:flex'}"
					data-card-actions
				>
					<Button size="xs" variant="primary" onclick={stop(() => onInsert(segment))}>Insert</Button>
					<Button size="xs" variant="secondary" onclick={stop(() => onOpen(segment))}>Edit</Button>
					<div class="relative" bind:this={menuEl}>
						<Tooltip text="More actions">
							<IconButton
								icon="more"
								label="More actions"
								size="sm"
								ariaExpanded={menuOpen}
								onclick={stop(() => (menuOpen = !menuOpen))}
							/>
						</Tooltip>
						{#if menuOpen}
							<div
								class="absolute right-0 top-[calc(100%+4px)] z-30 min-w-[180px] overflow-hidden rounded-xl border border-line-strong bg-surface-2 py-1 shadow-floating"
								role="menu"
							>
								<button
									type="button"
									role="menuitem"
									class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-fg-muted hover:bg-surface-3 hover:text-fg"
									onclick={stop(() => {
										closeMenu();
										onDuplicate(segment);
									})}
								>
									<Icon name="copy" className="h-3.5 w-3.5" />
									Duplicate
								</button>
								<div class="my-1 border-t border-line"></div>
								<button
									type="button"
									role="menuitem"
									class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-danger hover:bg-danger/10"
									onclick={stop(() => {
										closeMenu();
										onDelete(segment);
									})}
								>
									<Icon name="trash" className="h-3.5 w-3.5" />
									Delete
								</button>
							</div>
						{/if}
					</div>
				</div>
			</div>
		</div>
	</div>
</div>
