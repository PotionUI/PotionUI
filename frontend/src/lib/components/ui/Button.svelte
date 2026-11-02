<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '../Icon.svelte';

	type Variant = 'primary' | 'secondary' | 'ghost' | 'danger' | 'overlay';
	type Size = 'xs' | 'sm' | 'md' | 'lg';

	let {
		variant = 'secondary',
		size = 'md',
		icon,
		loading = false,
		disabled = false,
		href,
		target,
		rel,
		type = 'button',
		class: className = '',
		onclick,
		initialFocus = false,
		ariaExpanded,
		ariaHaspopup,
		ariaLabel,
		pressed,
		tabindex,
		attrs,
		children
	}: {
		variant?: Variant;
		size?: Size;
		icon?: string;
		loading?: boolean;
		disabled?: boolean;
		href?: string;
		target?: string;
		rel?: string;
		type?: 'button' | 'submit';
		class?: string;
		onclick?: (e: MouseEvent) => void;
		initialFocus?: boolean;
		ariaExpanded?: boolean;
		ariaHaspopup?: 'dialog' | 'menu' | 'listbox' | 'true';
		ariaLabel?: string;
		pressed?: boolean;
		tabindex?: number;
		attrs?: Record<string, string | number | boolean | undefined>;
		children?: Snippet;
	} = $props();

	const sizeClasses: Record<Size, string> = {
		xs: 'px-2 py-1 text-sm font-medium',
		sm: 'px-3 py-1.5 text-sm font-medium',
		md: 'px-4 py-2 text-base font-medium',
		lg: 'h-11 px-4 text-md font-semibold'
	};

	const variantClasses: Record<Variant, string> = {
		primary: 'bg-accent text-accent-contrast hover:bg-accent-hover active:bg-accent-active',
		secondary: 'bg-surface-3 text-fg hover:bg-line-hover',
		ghost: 'text-fg-muted hover:text-fg hover:bg-surface-3/50',
		danger: 'bg-danger-solid text-white hover:bg-danger-solid/90',
		overlay: 'bg-scrim/60 text-scrim-on backdrop-blur-sm hover:bg-scrim/80'
	};

	let pressedClasses = $derived(variant === 'overlay' ? 'bg-scrim-on/15 text-scrim-on' : 'bg-signal/10 text-signal');

	const iconSizeClasses: Record<Size, string> = {
		xs: 'w-3.5 h-3.5',
		sm: 'w-4 h-4',
		md: 'w-4 h-4',
		lg: 'w-4 h-4'
	};

	let isDisabled = $derived(disabled || loading);
	let classes = $derived(
		`inline-flex items-center justify-center gap-1.5 rounded transition-colors duration-100 touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap ${sizeClasses[size]} ${pressed ? pressedClasses : variantClasses[variant]} ${className}`
	);
</script>

{#if href && !isDisabled}
	<a {href} {target} {rel} class={classes} role="button" aria-label={ariaLabel}>
		{#if icon}<Icon name={icon} className={iconSizeClasses[size]} />{/if}
		{@render children?.()}
	</a>
{:else}
	<button
		{type}
		class={classes}
		disabled={isDisabled}
		{onclick}
		aria-expanded={ariaExpanded}
		aria-haspopup={ariaHaspopup}
		aria-label={ariaLabel}
		aria-pressed={pressed}
		{tabindex}
		data-autofocus={initialFocus ? '' : undefined}
		{...attrs}
	>
		{#if loading}
			<span
				class="{iconSizeClasses[
					size
				]} rounded-full border-2 border-line-strong border-t-current animate-spin"
			></span>
		{:else if icon}
			<Icon name={icon} className={iconSizeClasses[size]} />
		{/if}
		{@render children?.()}
	</button>
{/if}
