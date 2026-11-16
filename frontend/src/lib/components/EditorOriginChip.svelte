<script lang="ts">
	import Icon from '$lib/components/Icon.svelte';
	import tooltip from '$lib/actions/tooltip';
	import { toasts } from '$lib/stores/toast';
	import { DELETED_LABEL, ORIGIN_LABEL, openOriginEdit, type EditorOrigin } from '$lib/tool-tabs/editOrigin';

	let {
		origin,
		iconOnly = false,
		class: className = '',
		onopened
	}: {
		origin: EditorOrigin;
		iconOnly?: boolean;
		class?: string;
		onopened?: () => void;
	} = $props();

	let busy = $state(false);
	let gone = $state(false);

	async function open(event: MouseEvent) {
		event.stopPropagation();
		if (busy || gone) return;
		busy = true;
		try {
			const result = await openOriginEdit(origin);
			if (result.ok) {
				onopened?.();
				return;
			}
			if (result.kind === 'missing') gone = true;
			toasts.error(result.message);
		} finally {
			busy = false;
		}
	}

	let label = $derived(gone ? DELETED_LABEL : ORIGIN_LABEL);
</script>

{#if gone}
	<span
		class="inline-flex h-6 max-w-full items-center gap-1.5 rounded border border-line bg-surface-1 px-2 text-sm text-fg-subtle {className}"
		role="status"
		data-editor-origin="deleted"
	>
		<Icon name="sliders" className="h-3.5 w-3.5 flex-none" />
		{#if !iconOnly}<span class="min-w-0 truncate">{label}</span>{/if}
	</span>
{:else}
	<button
		type="button"
		class="inline-flex h-6 max-w-full items-center gap-1.5 rounded border border-line bg-surface-2 px-2 text-sm text-fg-muted transition-colors hover:border-line-hover hover:text-fg focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-signal disabled:opacity-60 {className}"
		aria-label={label}
		disabled={busy}
		onclick={open}
		use:tooltip={iconOnly ? 'Made in the editor. Open the edit.' : null}
		data-editor-origin="open"
	>
		<Icon name="sliders" className="h-3.5 w-3.5 flex-none" />
		{#if !iconOnly}<span class="min-w-0 truncate">{label}</span>{/if}
	</button>
{/if}
