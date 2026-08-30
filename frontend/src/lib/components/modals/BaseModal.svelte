<script lang="ts">
	import { createEventDispatcher, onDestroy } from 'svelte';
	import { fade, scale } from 'svelte/transition';
	import focusTrap from '$lib/actions/focusTrap';
	import portal from '$lib/actions/portal';
	import { IconButton } from '$lib/components/ui';

	// Size presets
	type ModalSize = 'sm' | 'md' | 'lg' | 'xl' | 'full';

	export let isOpen: boolean = false;
	export let title: string = '';
	/** One-line description rendered under the title, before the border. */
	export let subtitle: string = '';
	export let size: ModalSize = 'md';
	/** Override the size preset with a custom class string (e.g. 'md:w-[85vw] md:h-[85vh]'). */
	export let sizeClass: string = '';
	/**
	 * When true, clicking the backdrop or pressing Escape dispatches the 'close'
	 * event. Set to false if you want to handle closing yourself.
	 */
	export let closeable: boolean = true;
	/** Hide the default close (X) button in the header. */
	export let hideCloseButton: boolean = false;
	/** Use "alertdialog" for interruptive confirmations that demand a response. */
	export let dialogRole: 'dialog' | 'alertdialog' = 'dialog';
	/** Id of the element labelling the dialog, when the label lives in the slot. */
	export let labelledBy: string | undefined = undefined;
	/** Set false when the caller owns Escape itself (e.g. ConfirmModal), so it isn't handled twice. */
	export let handleEscapeKey: boolean = true;

	const dispatch = createEventDispatcher<{ close: void }>();

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const motionDuration = prefersReducedMotion ? 0 : 150;

	const sizeClasses: Record<ModalSize, string> = {
		sm: 'md:max-w-sm md:w-full',
		md: 'md:max-w-lg md:w-full',
		lg: 'md:max-w-3xl md:w-full',
		xl: 'md:max-w-5xl md:w-full',
		full: 'md:w-[90vw] md:h-[90vh]'
	};

	$: appliedSizeClass = sizeClass || sizeClasses[size];
	// If the size class sets an explicit md: height, don't also emit md:h-auto —
	// with two same-specificity height utilities the winner depends on stylesheet
	// order, and h-auto winning collapses every h-full descendant (huge media).
	$: hasExplicitMdHeight = /md:h-(\[|full|screen)/.test(appliedSizeClass);
	$: dialogClasses = [
		'relative bg-surface-1 md:rounded-xl shadow-overlay flex flex-col',
		'w-full h-full md:max-h-[90vh]',
		'pb-[env(safe-area-inset-bottom)] md:pb-0',
		hasExplicitMdHeight ? '' : 'md:h-auto',
		appliedSizeClass
	]
		.filter(Boolean)
		.join(' ');

	// Body scroll lock
	$: if (typeof document !== 'undefined') {
		if (isOpen) {
			document.body.style.overflow = 'hidden';
		} else {
			document.body.style.overflow = '';
		}
	}

	onDestroy(() => {
		if (typeof document !== 'undefined') {
			document.body.style.overflow = '';
		}
	});

	function handleClose() {
		if (closeable) dispatch('close');
	}

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) handleClose();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (!isOpen || !handleEscapeKey) return;
		if (e.key === 'Escape') {
			e.preventDefault();
			handleClose();
		}
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

{#if isOpen}
	<!-- Backdrop. Portaled to <body> — callers mount BaseModal from all over
	     the tree, including inside the mobile generate carousel's transformed
	     panel track, which would otherwise become the containing block for
	     this `position: fixed` and size/position the modal against the track
	     instead of the viewport. -->
	<div
		use:portal
		class="fixed inset-0 z-[9999] flex md:items-center md:justify-center bg-black/60 backdrop-blur-sm"
		role="button"
		tabindex="-1"
		aria-label="Close modal"
		on:click={handleBackdropClick}
		on:keydown={(e) => { if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); handleClose(); } }}
		transition:fade={{ duration: motionDuration }}
	>
		<!-- Dialog -->
		<div
			class={dialogClasses}
			role={dialogRole}
			aria-modal="true"
			aria-label={labelledBy ? undefined : title || undefined}
			aria-labelledby={labelledBy}
			tabindex="-1"
			on:click|stopPropagation
			on:keydown|stopPropagation
			transition:scale={{ duration: motionDuration, start: 0.96 }}
			use:focusTrap
		>
			<!-- Header (rendered only when title or close button is needed) -->
			{#if title || subtitle || $$slots.header || (!hideCloseButton && closeable)}
				<div class="flex items-center justify-between px-4 py-3 md:px-6 md:py-4 border-b border-line flex-shrink-0">
					<div class="flex flex-wrap items-center gap-3 flex-1 min-w-0">
						{#if $$slots.headerIcon}
							<slot name="headerIcon" />
						{/if}
						{#if title || subtitle}
							<div class="min-w-0 flex-1">
								{#if title}
									<h2 class="text-lg font-semibold text-fg truncate">{title}</h2>
								{/if}
								{#if subtitle}
									<p class="text-xs text-fg-muted mt-0.5 truncate">{subtitle}</p>
								{/if}
							</div>
						{/if}
						<slot name="header" />
					</div>
					{#if !hideCloseButton && closeable}
						<IconButton icon="close" label="Close modal" onclick={handleClose} />
					{/if}
				</div>
			{/if}

			<!-- Content -->
			<div class="flex-1 overflow-auto min-h-0">
				<slot />
			</div>

			<!-- Footer (optional) -->
			{#if $$slots.footer}
				<div class="flex-shrink-0 border-t border-line">
					<slot name="footer" />
				</div>
			{/if}
		</div>
	</div>
{/if}
