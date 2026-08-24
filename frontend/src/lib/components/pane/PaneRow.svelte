<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '$lib/components/Icon.svelte';
	import { Badge } from '$lib/components/ui';
	import { placeholderTint } from '$lib/utils/placeholderTint';

	type Size = 'md' | 'sm';
	type Role = 'option' | 'treeitem';
	type ThumbSize = 'sm' | 'md' | 'lg';

	let {
		onclick,
		selected = false,
		disabled = false,
		inactive = false,
		inactiveBadge,
		loading = false,
		size = 'md',
		role = 'option',
		depth = 0,
		expandable = false,
		expanded = false,
		onToggle,
		checkable = false,
		checked = false,
		onCheck,
		checkboxSpacer = false,
		icon,
		dot,
		thumbnail,
		thumbSize,
		thumbFallback,
		title,
		subtitle,
		subtitleMono = false,
		count,
		revealActions = false,
		class: className = '',
		leading,
		children,
		badges,
		meta,
		trailing,
		actions,
		preview
	}: {
		onclick?: () => void;
		selected?: boolean;
		disabled?: boolean;
		inactive?: boolean;
		inactiveBadge?: string;
		loading?: boolean;
		size?: Size;
		role?: Role;
		depth?: number;
		expandable?: boolean;
		expanded?: boolean;
		onToggle?: () => void;
		checkable?: boolean;
		checked?: boolean;
		onCheck?: (checked: boolean) => void;
		checkboxSpacer?: boolean;
		icon?: string;
		dot?: string;
		thumbnail?: string;
		/** No default: it distinguishes "unset" (no placeholder chip) from
		 *  an explicit size. Sizing falls back to 'md' at render time. */
		thumbSize?: ThumbSize;
		/** Text to derive an initial-letter fallback tile from when there's no
		 *  thumbnail. Unset keeps the generic icon fallback. */
		thumbFallback?: string;
		title?: string;
		subtitle?: string;
		subtitleMono?: boolean;
		count?: number | string;
		revealActions?: boolean;
		class?: string;
		leading?: Snippet;
		children?: Snippet;
		badges?: Snippet;
		meta?: Snippet;
		trailing?: Snippet;
		actions?: Snippet;
		preview?: Snippet;
	} = $props();

	const thumbSizeClasses: Record<ThumbSize, string> = {
		sm: 'w-6 h-6',
		md: 'w-10 h-10',
		lg: 'w-16 h-16'
	};

	const thumbIconSizeClasses: Record<ThumbSize, string> = {
		sm: 'w-3 h-3',
		md: 'w-4 h-4',
		lg: 'w-5 h-5'
	};

	// icon / dot / thumbnail: first one the consumer actually set wins; the
	// `leading` snippet always overrides all of them.
	let leadingKind = $derived(
		icon !== undefined
			? 'icon'
			: dot !== undefined
				? 'dot'
				: thumbnail !== undefined || thumbSize !== undefined
					? 'thumbnail'
					: 'none'
	);

	let showChevronSlot = $derived(expandable || loading || depth > 0);
	let indentPx = $derived(size === 'sm' ? depth * 12 + 2 : depth * 12);

	function handleToggleClick(e: MouseEvent) {
		e.stopPropagation();
		onToggle?.();
	}

	function handleCheckClick(e: MouseEvent) {
		e.stopPropagation();
	}

	function handleCheckChange(e: Event) {
		onCheck?.((e.target as HTMLInputElement).checked);
	}

	function handleClick() {
		if (disabled) return;
		onclick?.();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (disabled) return;
		if (e.key === 'Enter' || e.key === ' ') {
			e.preventDefault();
			onclick?.();
		} else if (e.key === 'ArrowRight' && expandable && !expanded) {
			e.preventDefault();
			onToggle?.();
		} else if (e.key === 'ArrowLeft' && expandable && expanded) {
			e.preventDefault();
			onToggle?.();
		}
	}
</script>

<!-- role is always 'option' or 'treeitem' (both interactive) but comes from a
     dynamic prop, so the a11y linter can't verify that statically. -->
<!-- svelte-ignore a11y_no_noninteractive_tabindex -->
<div
	class="pane-row group relative flex items-center {size === 'md'
		? 'px-4 py-2.5 text-sm border-b border-line/50 last:border-none'
		: 'h-7 px-2 rounded text-xs'} {disabled ? 'opacity-50 cursor-not-allowed' : 'cursor-pointer'} {size ===
	'md'
		? selected
			? 'border-l-2 border-l-signal'
			: 'border-l-2 border-l-transparent hover:bg-surface-2/50'
		: selected
			? 'bg-signal/10 text-signal'
			: 'hover:bg-surface-3/40'} {className}"
	style={size === 'md' && selected
		? 'background: linear-gradient(180deg, rgb(var(--signal) / 0.12), rgb(var(--signal) / 0.04));'
		: undefined}
	data-pane-row
	data-disabled={disabled ? '' : undefined}
	{role}
	aria-selected={selected}
	aria-expanded={expandable ? expanded : undefined}
	aria-disabled={disabled}
	tabindex={disabled ? -1 : 0}
	onclick={handleClick}
	onkeydown={handleKeydown}
>
	{#if depth > 0}
		<span class="flex-shrink-0" style="width: {indentPx}px" aria-hidden="true"></span>
	{/if}

	{#if showChevronSlot}
		{#if loading}
			<span class="flex-shrink-0 flex items-center justify-center w-5 h-5">
				<span
					class="w-3 h-3 rounded-full border border-line-strong border-t-transparent animate-spin"
				></span>
			</span>
		{:else if expandable}
			<button
				type="button"
				class="flex-shrink-0 flex items-center justify-center w-5 h-5 rounded hover:bg-line-hover/50 transition-colors"
				aria-label={expanded ? 'Collapse' : 'Expand'}
				onclick={handleToggleClick}
				tabindex="-1"
			>
				<svg
					class="transition-transform {expanded ? 'rotate-90' : ''} {size === 'sm' ? 'w-3 h-3' : 'w-3.5 h-3.5'}"
					fill="none"
					stroke="currentColor"
					viewBox="0 0 24 24"
				>
					<path stroke-linecap="round" stroke-linejoin="round" stroke-width="2" d="M9 5l7 7-7 7" />
				</svg>
			</button>
		{:else}
			<span class="flex-shrink-0 w-5 h-5" aria-hidden="true"></span>
		{/if}
	{/if}

	{#if checkable}
		<span class="relative inline-flex w-3.5 h-3.5 mr-1.5 flex-shrink-0">
			<input
				type="checkbox"
				class="peer absolute inset-0 opacity-0 cursor-pointer"
				{checked}
				onclick={handleCheckClick}
				onchange={handleCheckChange}
			/>
			<span
				class="pointer-events-none absolute inset-0 rounded border border-line-hover bg-surface-2 transition-colors peer-checked:border-accent peer-checked:bg-accent peer-focus-visible:ring-2 peer-focus-visible:ring-accent peer-focus-visible:ring-offset-1 peer-focus-visible:ring-offset-canvas"
			></span>
			<svg
				class="pointer-events-none absolute inset-0 h-full w-full text-accent-contrast opacity-0 transition-opacity peer-checked:opacity-100"
				viewBox="0 0 15 15"
				fill="none"
				aria-hidden="true"
			>
				<path
					d="M3 7.5L6.5 11L12 4.5"
					stroke="currentColor"
					stroke-width="1.5"
					stroke-linecap="round"
					stroke-linejoin="round"
				/>
			</svg>
		</span>
	{:else if checkboxSpacer}
		<span class="flex-shrink-0 w-4 mr-1" aria-hidden="true"></span>
	{/if}

	<div class="flex items-center flex-1 min-w-0 gap-3">
		{#if leading}
			{@render leading()}
		{:else if leadingKind === 'icon'}
			<span class="flex-shrink-0 {inactive ? 'opacity-50' : ''}">
				<Icon
					name={icon as string}
					className="{size === 'sm' ? 'w-3.5 h-3.5' : 'w-4 h-4'} {selected ? 'text-signal' : 'text-fg-subtle'}"
				/>
			</span>
		{:else if leadingKind === 'dot'}
			<span
				class="flex-shrink-0 w-2 h-2 rounded-full {inactive ? 'opacity-50' : ''}"
				style="background-color: {dot};"
				aria-hidden="true"
			></span>
		{:else if leadingKind === 'thumbnail'}
			<span
				class="flex-shrink-0 rounded overflow-hidden border border-line flex items-center justify-center {thumbnail
					? 'bg-surface-2'
					: thumbFallback
						? ''
						: 'bg-surface-3'} {thumbSizeClasses[thumbSize ?? 'md']} {inactive ? 'opacity-50' : ''}"
				style={!thumbnail && thumbFallback ? placeholderTint(thumbFallback) : undefined}
			>
				{#if thumbnail}
					<img src={thumbnail} alt="" class="w-full h-full object-cover" />
				{:else}
					<Icon name="image" className="{thumbIconSizeClasses[thumbSize ?? 'md']} text-fg-subtle" />
				{/if}
			</span>
		{/if}

		<div class="flex-1 min-w-0 {inactive ? 'opacity-50' : ''}">
			{#if children}
				{@render children()}
			{:else}
				<div class="flex items-center gap-1.5 min-w-0">
					{#if title !== undefined}
						<span class="truncate {size === 'sm' ? '' : 'font-medium text-fg'}">{title}</span>
					{/if}
					{@render badges?.()}
					{#if inactive && inactiveBadge}
						<Badge size="sm">{inactiveBadge}</Badge>
					{/if}
				</div>
				{#if subtitle !== undefined}
					<div class="truncate text-fg-subtle {subtitleMono ? 'font-mono text-2xs' : 'text-xs mt-0.5'}">
						{subtitle}
					</div>
				{/if}
				{@render meta?.()}
			{/if}
		</div>
	</div>

	{#if count !== undefined}
		<span class="flex-shrink-0 ml-2 font-mono text-2xs tabular-nums text-fg-subtle">{count}</span>
	{/if}

	{@render trailing?.()}

	{#if actions}
		<div
			class="flex-shrink-0 flex items-center gap-1 ml-2 {revealActions
				? 'opacity-100'
				: 'opacity-0 group-hover:opacity-100 focus-within:opacity-100'}"
		>
			{@render actions()}
		</div>
	{/if}

	{#if preview}
		<div
			class="absolute left-full top-0 ml-2 z-50 pointer-events-none opacity-0 group-hover:opacity-100 transition-opacity rounded-lg shadow-floating"
		>
			{@render preview()}
		</div>
	{/if}
</div>

<style>
	.pane-row:focus {
		outline: none;
	}

	.pane-row:focus-visible {
		box-shadow: inset 0 0 0 2px rgb(var(--accent) / 0.2);
	}
</style>
