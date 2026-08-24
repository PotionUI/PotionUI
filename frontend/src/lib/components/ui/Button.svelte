<script lang="ts">
	import type { Snippet } from 'svelte';
	import Icon from '../Icon.svelte';

	type Variant = 'primary' | 'secondary' | 'ghost' | 'danger';
	type Size = 'xs' | 'sm' | 'md' | 'lg';

	let {
		variant = 'secondary',
		size = 'md',
		icon,
		loading = false,
		disabled = false,
		href,
		type = 'button',
		class: className = '',
		onclick,
		title,
		initialFocus = false,
		children
	}: {
		variant?: Variant;
		size?: Size;
		icon?: string;
		loading?: boolean;
		disabled?: boolean;
		href?: string;
		type?: 'button' | 'submit';
		class?: string;
		onclick?: (e: MouseEvent) => void;
		title?: string;
		initialFocus?: boolean;
		children?: Snippet;
	} = $props();

	const sizeClasses: Record<Size, string> = {
		xs: 'px-2.5 py-1 text-xs font-medium',
		sm: 'px-3 py-1.5 text-sm font-medium',
		md: 'px-4 py-2 text-base font-medium',
		lg: 'h-11 px-4 text-md font-semibold'
	};

	const variantClasses: Record<Variant, string> = {
		primary: 'bg-accent text-accent-contrast hover:bg-accent-hover active:bg-accent-active',
		secondary: 'bg-surface-3 text-fg hover:bg-line-hover',
		ghost: 'text-fg-muted hover:text-fg hover:bg-surface-3/50',
		danger: 'bg-danger-solid text-white hover:bg-danger-solid/90'
	};

	const iconSizeClasses: Record<Size, string> = {
		xs: 'w-3.5 h-3.5',
		sm: 'w-4 h-4',
		md: 'w-4 h-4',
		lg: 'w-4 h-4'
	};

	let isDisabled = $derived(disabled || loading);
	let classes = $derived(
		`inline-flex items-center justify-center gap-1.5 rounded transition-colors duration-100 touch-manipulation disabled:opacity-50 disabled:cursor-not-allowed whitespace-nowrap ${sizeClasses[size]} ${variantClasses[variant]} ${className}`
	);
</script>

{#if href && !isDisabled}
	<a {href} class={classes} {title} role="button">
		{#if icon}<Icon name={icon} className={iconSizeClasses[size]} />{/if}
		{@render children?.()}
	</a>
{:else}
	<button
		{type}
		class={classes}
		disabled={isDisabled}
		{title}
		{onclick}
		data-autofocus={initialFocus ? '' : undefined}
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
