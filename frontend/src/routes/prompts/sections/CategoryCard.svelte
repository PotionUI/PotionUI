<script lang="ts">
	import type { SegmentCategory } from '$lib/types/segments';
	import { Badge, Button, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';

	let {
		category,
		count,
		selected = false,
		onToggleSelect,
		onOpen,
		onNewSegment,
		onDelete
	}: {
		category: SegmentCategory;
		count: number;
		selected?: boolean;
		onToggleSelect: (category: SegmentCategory) => void;
		onOpen: (category: SegmentCategory) => void;
		onNewSegment: (category: SegmentCategory) => void;
		onDelete: (category: SegmentCategory) => void;
	} = $props();

	let menuOpen = $state(false);
	let menuEl: HTMLDivElement | undefined = $state();

	const footerLabel = $derived(count === 0 ? 'No segments' : count === 1 ? '1 segment' : `${count} segments`);
	const relativeLabel = $derived.by(() => {
		const when = category.updated_at ?? category.created_at;
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
			onOpen(category);
		} else if (event.key === ' ') {
			event.preventDefault();
			onToggleSelect(category);
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
	data-category-card
	aria-label={category.name}
	onclick={() => onOpen(category)}
	onkeydown={handleCardKeydown}
>
	<button
		type="button"
		class="mt-1 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-opacity {selected
			? 'border-accent bg-accent text-accent-contrast opacity-100'
			: 'border-line-hover bg-surface-2 text-transparent opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}"
		onclick={stop(() => onToggleSelect(category))}
		aria-label={selected ? 'Deselect category' : 'Select category'}
		aria-pressed={selected}
	>
		{#if selected}<Icon name="check" className="h-3 w-3" strokeWidth={3} />{/if}
	</button>

	<div class="flex min-w-0 flex-1 flex-col gap-1.5">
		<div class="flex min-w-0 items-center gap-2">
			<span class="h-2.5 w-2.5 flex-shrink-0 rounded-full" style="background: {category.color}"></span>
			<h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{category.name}</h3>
			<Badge size="sm"><span class="font-mono tabular-nums">{count}</span></Badge>
		</div>

		{#if category.description}
			<p class="line-clamp-2 text-sm leading-relaxed text-fg-muted">{category.description}</p>
		{:else}
			<p class="text-sm italic text-fg-subtle">No description</p>
		{/if}

		<div class="mt-auto flex h-6 min-w-0 items-center gap-2.5" data-card-footer>
			<span class="whitespace-nowrap font-mono text-xs tabular-nums text-fg-subtle">{footerLabel}</span>
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
					<Button size="xs" variant="secondary" onclick={stop(() => onNewSegment(category))}>New segment</Button>
					<Button size="xs" variant="secondary" onclick={stop(() => onOpen(category))}>Edit</Button>
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
										onNewSegment(category);
									})}
								>
									<Icon name="plus" className="h-3.5 w-3.5" />
									New segment here
								</button>
								<div class="my-1 border-t border-line"></div>
								<button
									type="button"
									role="menuitem"
									class="flex w-full items-center gap-2 px-3 py-2 text-left text-xs text-danger hover:bg-danger/10"
									onclick={stop(() => {
										closeMenu();
										onDelete(category);
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
