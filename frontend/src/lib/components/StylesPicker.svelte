<script lang="ts">
	import { createEventDispatcher } from 'svelte';
	import { api } from '$lib/services/api';
	import type { PresetStyle } from '$lib/types/api';
	import BaseModal from './modals/BaseModal.svelte';
	import Icon from './Icon.svelte';

	export let isOpen = false;
	export let presetId: string;
	export let styles: PresetStyle[] = [];
	/** The style id currently applied to the prompt, when it belongs to `presetId` — the
	 *  caller derives this from the tagged segments via `appliedStyleTag` in styleSegments.ts. */
	export let appliedStyleId: string | null = null;

	const dispatch = createEventDispatcher<{ close: void; apply: PresetStyle }>();

	let innerWidth = 1024;
	let gridEl: HTMLDivElement | undefined;

	// Mirrors the `grid-cols-*` breakpoints on `.styles-grid` below, so arrow-key
	// navigation moves one visual row at a time instead of jumping the flat list.
	$: columns = innerWidth >= 1024 ? 5 : innerWidth >= 768 ? 4 : innerWidth >= 640 ? 3 : 2;

	interface Group {
		category: string;
		items: PresetStyle[];
	}

	$: groups = groupByCategory(styles);
	$: flatOrder = groups.flatMap((group) => group.items);

	function groupByCategory(list: PresetStyle[]): Group[] {
		const order: string[] = [];
		const byCategory = new Map<string, PresetStyle[]>();
		for (const item of list) {
			const category = item.category || 'General';
			if (!byCategory.has(category)) {
				byCategory.set(category, []);
				order.push(category);
			}
			byCategory.get(category)!.push(item);
		}
		return order.map((category) => ({ category, items: byCategory.get(category)! }));
	}

	function tileId(style: PresetStyle): string {
		return `style-tile-${style.id}`;
	}

	function apply(style: PresetStyle) {
		dispatch('apply', style);
	}

	function handleGridKeydown(event: KeyboardEvent) {
		const key = event.key;
		if (key !== 'ArrowLeft' && key !== 'ArrowRight' && key !== 'ArrowUp' && key !== 'ArrowDown') return;
		const target = event.target as HTMLElement;
		const currentIndex = flatOrder.findIndex((style) => tileId(style) === target.id);
		if (currentIndex === -1) return;

		event.preventDefault();
		let nextIndex = currentIndex;
		if (key === 'ArrowLeft') nextIndex = currentIndex - 1;
		else if (key === 'ArrowRight') nextIndex = currentIndex + 1;
		else if (key === 'ArrowUp') nextIndex = currentIndex - columns;
		else if (key === 'ArrowDown') nextIndex = currentIndex + columns;

		if (nextIndex < 0 || nextIndex >= flatOrder.length) return;
		gridEl?.querySelector<HTMLButtonElement>(`#${CSS.escape(tileId(flatOrder[nextIndex]))}`)?.focus();
	}
</script>

<svelte:window bind:innerWidth />

<BaseModal {isOpen} title="Styles" subtitle="Wrap the prompt with a curated style" sizeClass="md:max-w-3xl md:w-full md:max-h-[85vh]" on:close={() => dispatch('close')}>
	<svelte:fragment slot="headerIcon"><Icon name="sparkles" className="h-5 w-5 text-fg-muted" /></svelte:fragment>

	<div
		class="p-4 sm:p-6 space-y-6"
		role="toolbar"
		aria-label="Styles"
		aria-orientation="horizontal"
		tabindex="-1"
		bind:this={gridEl}
		on:keydown={handleGridKeydown}
	>
		{#if styles.length === 0}
			<div class="rounded-lg border border-dashed border-line p-8 text-center text-sm text-fg-muted">
				This preset has no styles yet.
			</div>
		{:else}
			{#each groups as group (group.category)}
				<section>
					<h3 class="mb-3 text-xs font-semibold uppercase tracking-wide text-fg-subtle">{group.category}</h3>
					<div class="styles-grid grid grid-cols-2 sm:grid-cols-3 md:grid-cols-4 lg:grid-cols-5 gap-3">
						{#each group.items as style (style.id)}
							{@const applied = style.id === appliedStyleId}
							<button
								type="button"
								id={tileId(style)}
								class="group flex flex-col gap-1.5 rounded-lg text-left focus:outline-none focus-visible:ring-2 focus-visible:ring-accent/40"
								title={style.description || style.name}
								aria-pressed={applied}
								on:click={() => apply(style)}
							>
								<div
									class="relative aspect-square w-full overflow-hidden rounded-lg border bg-surface-2 {applied
										? 'border-signal ring-2 ring-signal/30'
										: 'border-line group-hover:border-line-hover'}"
								>
									{#if style.preview}
										<img
											src={api.getPresetAssetURL(presetId, style.preview, 'small')}
											alt={style.name}
											class="h-full w-full object-cover"
											loading="lazy"
										/>
									{:else}
										<div class="flex h-full w-full items-center justify-center text-2xl font-semibold text-fg-subtle">
											{style.name.charAt(0).toUpperCase()}
										</div>
									{/if}
									{#if applied}
										<div class="absolute right-1.5 top-1.5 flex h-5 w-5 items-center justify-center rounded-full bg-signal text-white">
											<Icon name="check" className="h-3 w-3" />
										</div>
									{/if}
								</div>
								<span class="truncate text-xs font-medium {applied ? 'text-signal' : 'text-fg'}">{style.name}</span>
							</button>
						{/each}
					</div>
				</section>
			{/each}
		{/if}
	</div>
</BaseModal>
