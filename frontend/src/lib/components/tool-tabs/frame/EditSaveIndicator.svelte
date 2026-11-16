<script lang="ts">
	import Tooltip from '$lib/components/Tooltip.svelte';
	import type { AutosaveStatus } from '$lib/tool-tabs/image/editAutosave';

	let { status, chip = false }: { status: AutosaveStatus; chip?: boolean } = $props();

	const copy: Record<Exclude<AutosaveStatus, 'idle'>, { label: string; hint: string; dot: string }> = {
		saved: { label: 'Saved', hint: 'Your edit is saved to your account.', dot: 'bg-success-solid' },
		saving: { label: 'Saving…', hint: 'Saving your edit.', dot: 'bg-signal-solid' },
		offline: {
			label: 'Offline',
			hint: 'Your changes are kept in this browser and saved as soon as the connection is back.',
			dot: 'bg-warning-solid'
		},
		failed: {
			label: 'Not saved',
			hint: 'Your edit could not be saved. Your changes are kept in this browser.',
			dot: 'bg-danger-solid'
		},
		conflict: {
			label: 'Changed elsewhere',
			hint: 'Choose which version to keep to continue saving.',
			dot: 'bg-danger-solid'
		},
		'too-large': {
			label: 'Not saved',
			hint: 'This edit is too big to save to your account. Your changes are kept in this browser.',
			dot: 'bg-danger-solid'
		}
	};

	let entry = $derived(status === 'idle' ? null : copy[status]);
</script>

{#if entry}
	<Tooltip text={entry.hint} position="bottom">
		<span
			class="inline-flex flex-none items-center gap-1.5 whitespace-nowrap text-sm {chip
				? 'h-7 rounded bg-scrim/70 px-2 text-scrim-on backdrop-blur'
				: status === 'failed' || status === 'conflict' || status === 'too-large'
					? 'text-danger'
					: 'text-fg-muted'}"
			role="status"
			data-edit-save-state={status}
		>
			<span class="h-2 w-2 flex-none rounded-full {entry.dot}" aria-hidden="true"></span>
			<span class={chip ? '' : 'max-[1100px]:sr-only'}>{entry.label}</span>
		</span>
	</Tooltip>
{/if}
