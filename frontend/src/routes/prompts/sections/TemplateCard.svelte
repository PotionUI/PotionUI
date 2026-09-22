<script lang="ts">
	import type { SegmentTemplate } from '$lib/types/segments';
	import { Badge, Button, IconButton } from '$lib/components/ui';
	import Icon from '$lib/components/Icon.svelte';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import { timeAgo } from '$lib/utils/relativeTime';
	import { richTextToPlainText } from '$lib/utils/richTextUtils';
	import { templateSlotCount, templateSlotLabels } from './templateFilters';

	const VISIBLE_SLOTS = 8;
	const AFFIX_CHARS = 12;

	let {
		template,
		selected = false,
		onToggleSelect,
		onOpen,
		onApply,
		onDuplicate,
		onDelete
	}: {
		template: SegmentTemplate;
		selected?: boolean;
		onToggleSelect: (template: SegmentTemplate) => void;
		onOpen: (template: SegmentTemplate) => void;
		onApply: (template: SegmentTemplate) => void;
		onDuplicate: (template: SegmentTemplate) => void;
		onDelete: (template: SegmentTemplate) => void;
	} = $props();

	let menuOpen = $state(false);
	let menuEl: HTMLDivElement | undefined = $state();

	const slotCount = $derived(templateSlotCount(template));
	const slotLabels = $derived(templateSlotLabels(template));
	const slotRows = $derived(
		(template.segments ?? []).map((segment, index) => ({
			segment,
			label: slotLabels[index],
			text: richTextToPlainText(segment.content || '', segment.chips || {}).trim()
		}))
	);
	const shownSlots = $derived(slotRows.slice(0, VISIBLE_SLOTS));
	const hiddenSlots = $derived(Math.max(0, slotRows.length - VISIBLE_SLOTS));
	const tags = $derived((template.tags ?? []).slice(0, 2));
	const relativeLabel = $derived.by(() => {
		const when = template.updated_at || template.created_at;
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
			onOpen(template);
		} else if (event.key === ' ') {
			event.preventDefault();
			onToggleSelect(template);
		}
	}

	function affix(value: string | null | undefined): string {
		const text = (value ?? '').trim();
		return text.length > AFFIX_CHARS ? `${text.slice(0, AFFIX_CHARS)}…` : text;
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
	class="group relative mb-3 flex min-w-0 break-inside-avoid gap-3 rounded-lg border p-3 shadow-raised transition-colors {selected
		? 'border-signal bg-signal/[0.06] shadow-[0_0_0_1px_rgb(var(--signal))]'
		: 'border-line-strong bg-surface-1 hover:border-line-hover'}"
	role="button"
	tabindex="0"
	data-template-card
	aria-label={template.name}
	onclick={() => onOpen(template)}
	onkeydown={handleCardKeydown}
>
	<button
		type="button"
		class="mt-1 flex h-4 w-4 flex-shrink-0 items-center justify-center rounded border transition-opacity {selected
			? 'border-accent bg-accent text-accent-contrast opacity-100'
			: 'border-line-hover bg-surface-2 text-transparent opacity-0 group-hover:opacity-100 group-focus-within:opacity-100'}"
		onclick={stop(() => onToggleSelect(template))}
		aria-label={selected ? 'Deselect template' : 'Select template'}
		aria-pressed={selected}
	>
		{#if selected}<Icon name="check" className="h-3 w-3" strokeWidth={3} />{/if}
	</button>

	<div class="flex min-w-0 flex-1 flex-col gap-2">
		<div class="flex min-w-0 items-center gap-2">
			<h3 class="min-w-0 flex-1 truncate text-sm font-semibold text-fg">{template.name}</h3>
		</div>

		{#if slotRows.length === 0}
			<p class="text-xs text-fg-subtle">No slots yet</p>
		{:else}
			<div class="space-y-1.5" data-template-slots>
				{#each shownSlots as row, index (index)}
					{#if row.segment.type === 'break'}
						<div
							class="flex items-center gap-3 py-1 text-xs uppercase tracking-wide text-fg-muted"
							data-template-slot="break"
						>
							<span class="h-px flex-1 bg-line"></span>
							Prompt break
							<span class="h-px flex-1 bg-line"></span>
						</div>
					{:else}
						<div
							class="min-w-0 rounded border border-line-strong bg-surface-2 px-2.5 py-1.5 {row.text
								? ''
								: 'border-dashed'} {row.segment.enabled === false ? 'opacity-60' : ''}"
							data-template-slot={row.text ? 'content' : 'empty'}
						>
							<div class="flex min-w-0 items-center gap-2">
								<span
									class="h-2 w-2 flex-shrink-0 rounded-full {row.segment.color ? '' : 'bg-fg-subtle'}"
									style={row.segment.color ? `background: ${row.segment.color}` : undefined}
								></span>
								<span class="min-w-0 flex-1 truncate text-xs font-medium text-fg">{row.label}</span>
								{#if row.segment.enabled === false}
									<span class="flex-shrink-0 font-mono text-xs text-fg-subtle" data-template-slot-off>Off</span>
								{/if}
							</div>
							<div class="mt-0.5 flex min-w-0 items-baseline gap-1 pl-4">
								{#if row.text && row.segment.prefix}
									<span class="flex-shrink-0 font-mono text-xs text-fg-subtle" data-template-slot-prefix
										>{affix(row.segment.prefix)}</span
									>
								{/if}
								<span class="min-w-0 truncate text-xs {row.text ? 'text-fg-muted' : 'text-fg-subtle'}">
									{row.text || 'Empty slot'}
								</span>
								{#if row.text && row.segment.suffix}
									<span class="flex-shrink-0 font-mono text-xs text-fg-subtle" data-template-slot-suffix
										>{affix(row.segment.suffix)}</span
									>
								{/if}
							</div>
						</div>
					{/if}
				{/each}
				{#if hiddenSlots > 0}
					<p class="px-2.5 text-xs text-fg-subtle" data-template-slots-more>
						+<span class="font-mono tabular-nums">{hiddenSlots}</span> more slot{hiddenSlots === 1 ? '' : 's'}
					</p>
				{/if}
			</div>
		{/if}

		<div class="mt-auto flex h-6 min-w-0 items-center gap-1.5" data-card-footer>
			<Badge size="sm" variant="signal">
				<span class="font-mono tabular-nums">{slotCount}</span>
				slot{slotCount === 1 ? '' : 's'}
			</Badge>
			{#each tags as tag (tag)}
				<Badge size="sm">{tag}</Badge>
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
					<Button size="xs" variant="primary" onclick={stop(() => onApply(template))}>Apply</Button>
					<Button size="xs" variant="secondary" onclick={stop(() => onOpen(template))}>Edit</Button>
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
										onDuplicate(template);
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
										onDelete(template);
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
