<script lang="ts">
	/**
	 * The frame every media editor sits in.
	 *
	 * All four editors are overlays over the thing being edited, so they share
	 * one shell: title bar naming the tool and the file, a stage, and a footer
	 * that carries the save choice. Built on BaseModal so they inherit the focus
	 * trap, the escape handling and the backdrop the rest of the app's overlays
	 * already have rather than growing a fourth implementation of them.
	 */
	import BaseModal from '$lib/components/modals/BaseModal.svelte';
	import { EDITOR_ICONS, type EditorIconName } from './editorIcons';

	export let title: string;
	export let fileName: string;
	export let icon: EditorIconName;
	/** Tailwind width for the dialog - a crop stage needs more room than a trim rail. */
	export let widthClass: string = 'md:w-[min(60rem,92vw)]';
	export let onClose: () => void;
	/** Named so a save in flight cannot be interrupted half-way by the backdrop. */
	export let busy: boolean = false;
</script>

<BaseModal
	isOpen={true}
	{title}
	sizeClass={widthClass}
	closeable={!busy}
	on:close={onClose}
>
	<svelte:fragment slot="headerIcon">
		<svg class="w-3.5 h-3.5 text-signal" fill="none" viewBox="0 0 24 24" stroke="currentColor">
			<path stroke-linecap="round" stroke-linejoin="round" stroke-width="1.8" d={EDITOR_ICONS[icon]} />
		</svg>
	</svelte:fragment>

	<svelte:fragment slot="header">
		<span class="hidden sm:block min-w-0 truncate font-mono text-2xs tracking-[0.05em] text-fg-subtle" title={fileName}>
			{fileName}
		</span>
	</svelte:fragment>

	<slot />

	<svelte:fragment slot="footer">
		<div class="flex flex-wrap items-center gap-2 px-4 py-3 bg-surface-1">
			<slot name="footer" />
		</div>
	</svelte:fragment>
</BaseModal>
