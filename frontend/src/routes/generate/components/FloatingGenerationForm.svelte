<script lang="ts">
	import { fade, scale } from 'svelte/transition';
	import focusTrap from '$lib/actions/focusTrap';
	import portal from '$lib/actions/portal';
	import { IconButton } from '$lib/components/ui';
	import Tooltip from '$lib/components/Tooltip.svelte';
	import GenerationFormPane from './GenerationFormPane.svelte';
	import type DynamicForm from '$lib/components/DynamicForm.svelte';
	import type { Tab } from '$lib/types/tabs';

	// The "quick" (Q) floating form: the same GenerationFormPane the inline
	// left panel renders, shown as a modal overlay instead. Mounted only
	// while `tab.formFloating` is true, at which point the inline panel is
	// folded (`leftPanelCollapsed`) and unmounted — so there is never more
	// than one live DynamicForm instance for a tab to diverge from.
	let {
		tab,
		presetName,
		videoDirectorActive = false,
		dynamicFormRefs,
		onFormDataChange,
		onClose,
		width,
		closeShortcut = undefined
	}: {
		tab: Tab;
		presetName: string | undefined;
		videoDirectorActive?: boolean;
		dynamicFormRefs: Record<string, DynamicForm>;
		onFormDataChange: (data: Record<string, unknown>) => void;
		onClose: () => void;
		/** The inline form pane's width — the form is designed for it, so the overlay keeps it. */
		width: number;
		closeShortcut?: string;
	} = $props();

	const prefersReducedMotion =
		typeof window !== 'undefined' &&
		typeof window.matchMedia === 'function' &&
		window.matchMedia('(prefers-reduced-motion: reduce)').matches;
	const motionDuration = prefersReducedMotion ? 0 : 150;

	function handleBackdropClick(e: MouseEvent) {
		if (e.target === e.currentTarget) onClose();
	}

	function handleKeydown(e: KeyboardEvent) {
		if (e.key === 'Escape') {
			e.preventDefault();
			onClose();
		}
	}
</script>

<svelte:window on:keydown|capture={handleKeydown} />

<div
	use:portal
	class="fixed inset-0 z-[9999] flex items-end justify-center bg-black/60 backdrop-blur-sm md:items-center"
	role="button"
	tabindex="-1"
	aria-label="Close floating generation form"
	onclick={handleBackdropClick}
	onkeydown={(e) => {
		if (e.target === e.currentTarget && (e.key === 'Enter' || e.key === ' ')) {
			e.preventDefault();
			onClose();
		}
	}}
	transition:fade={{ duration: motionDuration }}
>
	<div
		class="flex h-[85vh] w-full flex-col rounded-t-xl bg-surface-1 shadow-overlay md:h-auto md:max-h-[85vh] md:w-[var(--floating-form-width)] md:rounded-xl"
		style="--floating-form-width: min({width}px, 92vw)"
		role="dialog"
		aria-modal="true"
		aria-label="Generation form"
		tabindex="-1"
		onclick={(e) => e.stopPropagation()}
		onkeydown={(e) => e.stopPropagation()}
		transition:scale={{ duration: motionDuration, start: 0.96 }}
		use:focusTrap
	>
		<div class="flex flex-shrink-0 items-center justify-between border-b border-line px-4 py-3 md:px-6 md:py-4">
			<div class="min-w-0 flex-1">
				<h2 class="truncate text-lg font-semibold text-fg">{presetName ?? 'Generation form'}</h2>
			</div>
			<Tooltip text="Close" kbd={closeShortcut} position="left" delay={150}>
				<IconButton icon="close" label="Close floating generation form" onclick={onClose} />
			</Tooltip>
		</div>
		<div class="min-h-0 flex-1 overflow-y-auto">
			<GenerationFormPane
				bind:formRef={dynamicFormRefs[tab.id]}
				{tab}
				{videoDirectorActive}
				{onFormDataChange}
			/>
		</div>
	</div>
</div>
