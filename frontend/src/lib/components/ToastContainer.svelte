<script lang="ts">
	import { fly, fade } from 'svelte/transition';
	import { toasts, capToasts, toastDisplayTitle, toastsAriaLive } from '$lib/stores/toast';
	import type { Toast, ToastType } from '$lib/stores/toast';
	import Icon from '$lib/components/Icon.svelte';

	const iconByType: Record<ToastType, string> = {
		success: 'check',
		error: 'close',
		info: 'info',
		warning: 'warning'
	};

	const ruleColorByType: Record<ToastType, string> = {
		success: 'bg-success',
		error: 'bg-danger',
		info: 'bg-fg-muted',
		warning: 'bg-fg-muted'
	};

	const iconColorByType: Record<ToastType, string> = {
		success: 'text-success',
		error: 'text-danger',
		info: 'text-fg-muted',
		warning: 'text-fg-muted'
	};

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;

	function progressFill(node: HTMLElement, duration: number) {
		node.style.transitionProperty = 'width';
		node.style.transitionTimingFunction = 'linear';
		node.style.transitionDuration = prefersReducedMotion ? '0ms' : `${duration}ms`;
		node.style.width = '100%';
		requestAnimationFrame(() => {
			requestAnimationFrame(() => {
				node.style.width = '0%';
			});
		});
		return { destroy() {} };
	}

	function showsMessage(toast: Toast) {
		return Boolean(toast.title) || !toast.count || toast.count <= 1;
	}

	let capped = $derived(capToasts($toasts));
	let ariaLive = $derived(toastsAriaLive($toasts));
</script>

<div
	class="fixed top-6 right-6 z-[9998] flex w-80 flex-col gap-2 pointer-events-none"
	role="status"
	aria-live={ariaLive}
>
	{#each capped.visible as toast (toast.id)}
		<div
			class="pointer-events-auto relative flex w-full items-start gap-2.5 rounded-lg border border-line-strong bg-surface-2 py-2.5 pl-3.5 pr-3 shadow-floating"
			in:fly={{ y: prefersReducedMotion ? 0 : -24, x: prefersReducedMotion ? 0 : 24, duration: prefersReducedMotion ? 0 : 250 }}
			out:fade={{ duration: prefersReducedMotion ? 0 : 200 }}
		>
			<span class="absolute left-0 top-1.5 bottom-1.5 w-0.5 rounded-sm {ruleColorByType[toast.type]}"></span>

			<Icon name={iconByType[toast.type]} className="mt-px h-[15px] w-[15px] flex-shrink-0 {iconColorByType[toast.type]}" />

			<div class="min-w-0 flex-1">
				{#if toastDisplayTitle(toast)}
					<p class="text-[12.5px] font-medium leading-snug text-fg">{toastDisplayTitle(toast)}</p>
				{/if}
				{#if showsMessage(toast)}
					<p class="mt-px text-xs leading-[1.4] text-fg-muted">{toast.message}</p>
				{/if}
				{#if toast.action}
					<div class="mt-2 flex items-center gap-2">
						<button
							class="rounded px-[9px] py-[3px] text-[11px] font-medium bg-accent text-accent-contrast"
							onclick={toast.action.onClick}
						>
							{toast.action.label}
						</button>
					</div>
				{/if}
				{#if toast.duration && toast.duration > 0}
					<div class="toast-progress mt-2 h-0.5 overflow-hidden rounded-sm bg-surface-3">
						<div class="h-full bg-fg-disabled" use:progressFill={toast.duration}></div>
					</div>
				{/if}
			</div>

			<button
				class="flex h-5 w-5 flex-shrink-0 items-center justify-center rounded text-fg-subtle hover:bg-surface-3 hover:text-fg"
				onclick={() => toasts.remove(toast.id)}
				aria-label="Dismiss"
			>
				<Icon name="close" className="h-3 w-3" />
			</button>
		</div>
	{/each}

	{#if capped.overflowCount > 0}
		<div
			class="pointer-events-auto self-end rounded border border-line-strong bg-surface-1 px-2 py-[3px] font-mono text-[10px] tracking-[0.04em] text-fg-subtle"
		>
			+{capped.overflowCount} more
		</div>
	{/if}
</div>
