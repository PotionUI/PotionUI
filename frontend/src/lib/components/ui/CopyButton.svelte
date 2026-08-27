<script lang="ts">
	import { getContext } from 'svelte';
	import Icon from '../Icon.svelte';
	import { INSIDE_TOOLTIP_CONTEXT_KEY } from '../tooltipContext';
	import { copyText } from '$lib/utils/clipboard';
	import { toasts } from '$lib/stores/toast';

	type Size = 'xs' | 'sm' | 'md' | 'lg';
	type Variant = 'ghost' | 'secondary' | 'primary';

	let {
		text,
		label,
		title,
		ariaLabel,
		size = 'sm',
		variant = 'ghost',
		disabled = false,
		class: className = ''
	}: {
		/** Value to copy, or a function returning it (resolved on click). */
		text: string | (() => string);
		/** Renders a labeled button (icon + text) instead of icon-only. */
		label?: string;
		/** Native tooltip. Also the icon-only aria-label fallback when `ariaLabel` is absent. */
		title?: string;
		/** Explicit accessible name for icon-only mode; defaults to `title`, then "Copy". Ignored when `label` is set - the visible text is the accessible name. */
		ariaLabel?: string;
		size?: Size;
		variant?: Variant;
		disabled?: boolean;
		class?: string;
	} = $props();

	// A wrapping Tooltip already shows the accessible name; keeping the native
	// title too renders two tooltips side by side (see IconButton.svelte).
	const insideTooltip = getContext<boolean | undefined>(INSIDE_TOOLTIP_CONTEXT_KEY) === true;

	let copied = $state(false);
	let timer: ReturnType<typeof setTimeout> | undefined;

	async function handleClick(e: MouseEvent) {
		e.stopPropagation();
		const value = typeof text === 'function' ? text() : text;
		if (!value) return;
		const ok = await copyText(value);
		if (ok) {
			copied = true;
			clearTimeout(timer);
			timer = setTimeout(() => (copied = false), 1500);
		} else {
			toasts.error('Could not copy');
		}
	}

	const iconOnlySizeClasses: Record<Size, string> = {
		xs: 'min-w-6 min-h-6 p-1',
		sm: 'min-w-8 min-h-8 p-1.5',
		md: 'min-w-10 min-h-10 p-2',
		lg: 'min-w-10 min-h-10 p-2'
	};

	const iconSizeClasses: Record<Size, string> = {
		xs: 'w-3.5 h-3.5',
		sm: 'w-4 h-4',
		md: 'w-5 h-5',
		lg: 'w-5 h-5'
	};

	const labeledSizeClasses: Record<Size, string> = {
		xs: 'px-2.5 py-1 text-xs font-medium gap-1.5',
		sm: 'px-3 py-1.5 text-sm font-medium gap-1.5',
		md: 'px-4 py-2 text-base font-medium gap-1.5',
		lg: 'h-11 px-4 text-md font-semibold gap-1.5'
	};

	const variantClasses: Record<Variant, string> = {
		ghost: 'text-fg-muted hover:text-fg hover:bg-surface-3/50',
		secondary: 'bg-surface-3 text-fg hover:bg-line-hover',
		primary: 'bg-accent text-accent-contrast hover:bg-accent-hover active:bg-accent-active'
	};

	let resolvedAriaLabel = $derived(ariaLabel ?? title ?? 'Copy');
	let classes = $derived(
		`inline-flex items-center justify-center rounded transition-colors duration-100 touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap ${label ? labeledSizeClasses[size] : iconOnlySizeClasses[size]} ${variantClasses[variant]} ${className}`
	);
</script>

<button
	type="button"
	class={classes}
	aria-label={label ? undefined : resolvedAriaLabel}
	title={insideTooltip ? undefined : (label ? title : resolvedAriaLabel)}
	{disabled}
	onclick={handleClick}
>
	<Icon name={copied ? 'check' : 'copy'} className={iconSizeClasses[size]} />
	{#if label}<span>{copied ? 'Copied' : label}</span>{/if}
</button>
